# [T134] The account check: a console-owned pin, not a copy of another tool's guard

milestone: v3.0.1 · track: github · depends_on: T132, T133, T04, T97 · provides: `github_adapter/account.py` + `GitHubSettings` in `config.py` — resolves the `gh` executable and the account it will act as, refuses to invoke `gh` when a configured pin does not match the active account, and names each refusal (`gh_absent` | `gh_unauthenticated` | `gh_account_mismatch`).

## Context

ARCHITECTURE.md asks for PR status read via "guard-scoped `gh` (respects the dual-account pin)". The
obvious reading — shell out to the guard — does not survive investigation, and the obvious
alternative — re-implement the guard — is worse. Both are recorded here so the decision is not
relitigated.

**There is no guard executable.** The dual-account guard on this machine is
`.claude/hooks/gh-account-guard.sh`, a Claude Code **PreToolUse hook**, not a wrapper binary on
`PATH` (`which -a gh` finds only the real `gh`). A server process has nothing to call.

**And it must not be copied.** The hook resolves its pin as `GH_GUARD_USER` →
`$XDG_CONFIG_HOME/app-factory/account` (uncommitted, machine-local, owned by another tool, whose own
comment says "keep this path in sync with lib/common.sh") → a baked default that is empty in a
factory-init'd repo, where "an empty WANT fails closed". Re-implementing that order inside a wheel
published to PyPI would (1) take a hard runtime dependency on another tool's private file layout, and
(2) mean that for every ordinary `uvx factory-console` user — no app-factory, no pin file — the check
fails closed and **`/api/v1/github/pulls` returns a degraded reason for 100% of users**, shipping this
milestone dead. The hook's WHERE axis is also non-transferable: it is baked to a single repo
(`ALLOWED_REPOS_RE`), while the console queries arbitrary registered projects. Duplicating a security
control into a second place is additionally the "same fact answered in two places" class T93/T97/T102
were filed against.

**So the pin becomes console-owned configuration**, and the WHERE axis is owned entirely by T135's
per-project `--repo owner/name`:

- **Pin set** (`FACTORY_CONSOLE_GITHUB_ACCOUNT`, or `GH_GUARD_USER` honoured as an optional
  parity override) → it must equal `gh`'s active `github.com` user, or the call is REFUSED with
  `gh_account_mismatch`. This is what "respects the dual-account pin" means for the console.
- **Pin unset** — the common case for an ordinary user — → proceed as whatever account `gh` is
  authenticated as, and **disclose it** in `GitHubSource.account` so an operator can always see which
  identity answered. Refusing here would be fail-closed against a risk that does not exist: with no
  pin configured, there is no "wrong identity" defined, and reading PRs as the account `gh` already
  holds is simply what `gh` does.

The console **never runs `gh auth switch`**: mutating the operator's global `gh` state on their behalf
is out of scope in every version, and would also make a read endpoint a writer.

## Staged approach

1. EDIT `server/factory_console/config.py`: add `class GitHubSettings(BaseSettings)` (env prefix
   `FACTORY_CONSOLE_`, following the `WriteTokenSettings` shape — a focused class readable without
   validating the rest of the config, for the reason `read_write_token()` exists) with
   `github_account: str | None = None`, `gh_path: str | None = None`,
   `gh_timeout_seconds: float = 6.0` (validated `> 0`, `<= 60`), and `read_github_settings() ->
   GitHubSettings`. **`config.py` is a shared file** — declared here; no other v3.0.1 ticket touches
   it.
2. CREATE `server/factory_console/github_adapter/account.py` with the `# READ-ONLY:` banner.
3. `resolve_gh_executable(settings) -> Path | None`: `settings.gh_path` if set, else
   `shutil.which("gh")`. `None` → `gh_absent`.
4. `resolve_active_account() -> str | None`: bounded-read `hosts.yml` from
   `${GH_CONFIG_DIR:-${XDG_CONFIG_HOME:-~/.config}/gh}` (cap 256 KiB) via
   `file_adapter.bounded_read.read_bounded`, so this module never calls `open()` itself and inherits
   the descriptor gates; `yaml.safe_load`; take `doc["github.com"]["user"]` and IMMEDIATELY discard
   the rest of the document. **The file contains `oauth_token`; the parsed document must never be
   logged, never be put in an exception message, and never leave this function.** Unparseable /
   absent / missing key → `None` → `gh_unauthenticated`.
5. `resolve_pin(settings) -> str | None`: `settings.github_account`, else the `GH_GUARD_USER`
   environment variable if present (optional parity with the operator's existing habit), else `None`
   meaning "no pin configured".
6. `resolve_account(settings) -> ResolvedGh | GitHubSourceReason` returning a frozen
   `ResolvedGh { executable: Path, account: str, pinned: bool }`, evaluated in this order — executable
   → active account → pin comparison. The order is the monotone one: never report "wrong account" for
   a machine with no `gh`. An unset pin returns `pinned=False` with the active account; a set pin that
   differs returns `gh_account_mismatch`.
7. Log only the outcome NAME and, on a mismatch, the PINNED account (which the operator configured);
   never the active account, never any file content.
8. CREATE `tests/unit/test_github_account.py` using `tmp_path` + monkeypatched `XDG_CONFIG_HOME` /
   `GH_CONFIG_DIR` and a stub `which`: `gh_absent`; `gh_unauthenticated` (no `hosts.yml`, unparseable
   `hosts.yml`, a `hosts.yml` with a different host only); pin set and matching → success with
   `pinned=True`; pin set and differing → `gh_account_mismatch`; **pin unset → success with the active
   account and `pinned=False`** (the ships-for-ordinary-users regression test); a `hosts.yml` whose
   token must not appear in any log record (assert with `caplog`); and
   `assert_module_is_read_only`. No real `gh` is invoked at any point.

## Critical files

- `server/factory_console/config.py` (modify — shared file, only this v3.0.1 ticket touches it)
- `server/factory_console/github_adapter/account.py` (create)
- `tests/unit/test_github_account.py` (create)
- `tests/unit/test_config.py` (modify)

## Interface & data

`resolve_account(settings: GitHubSettings) -> ResolvedGh | GitHubSourceReason`;
`ResolvedGh { executable: Path, account: str, pinned: bool }`; `resolve_gh_executable`,
`resolve_active_account`, `resolve_pin`.

Config contract extended (not redesigned): `FACTORY_CONSOLE_GITHUB_ACCOUNT`,
`FACTORY_CONSOLE_GH_PATH`, `FACTORY_CONSOLE_GH_TIMEOUT_SECONDS` under the existing prefix; the
loopback host validator is NOT touched (that is v3.1).

External files READ, never written: `${GH_CONFIG_DIR:-${XDG_CONFIG_HOME:-~/.config}/gh}/hosts.yml`,
through `file_adapter.bounded_read.read_bounded`. **The console reads none of app-factory's files.**

Reasons produced (by reference to T131): `gh_absent`, `gh_unauthenticated`, `gh_account_mismatch`.

DB ops: none. NFR flags: pin honoured when set and disclosed either way; **secret handling** — an
OAuth token sits in the file being parsed and must never be logged or surfaced; bounded read; no
mutation of `gh` state.

## Verification

`python -m pytest tests/unit/test_github_account.py tests/unit/test_config.py -q`; `make lint`;
`python -m pytest -q` (the config change must not disturb existing settings tests).
Manual sanity, read-only and optional: `gh auth status` to see which account is active, and confirm
that with `FACTORY_CONSOLE_GITHUB_ACCOUNT` unset the resolver returns that account rather than
refusing. Do NOT run any mutating `gh` command.
