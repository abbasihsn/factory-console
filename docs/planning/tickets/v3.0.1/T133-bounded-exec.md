# [T133] Bounded, timed subprocess runner for the GitHub adapter

milestone: v3.0.1 · track: github · depends_on: T132, T97 · provides: `github_adapter/bounded_exec.py` — the single argv-only, timeout-bounded, output-capped, env-scrubbed, secret-safe subprocess sequence every `gh`/`git` invocation in this track goes through, plus the `CommandRunner` type that makes the whole adapter injectable and network-free under test.

## Context

A subprocess on a network call is unbounded in a way nothing else in this codebase is: it can hang
forever, print a gigabyte, or emit a token in stderr. Every existing artifact read is bounded by
`file_adapter/bounded_read.py`, which exists precisely because two readers each kept their own copy
of a read sequence and drifted (T97).

This ticket lands the equivalent ONE sequence for process execution **before any caller exists**, so
the timeout, the byte caps, the env scrub and the never-log-output rule are properties of the call and
not of whichever caller remembered them. It is also the seam that guarantees no test ever runs real
`gh`: callers take a `CommandRunner` and tests inject one.

## Staged approach

1. CREATE `server/factory_console/github_adapter/bounded_exec.py`, carrying the
   `# READ-ONLY: this module MUST NOT write, create, or delete.` banner — it executes only read
   commands and touches no path.
2. Declare the bounds as named module constants with the reasoning in their docstrings:
   `DEFAULT_TIMEOUT_SECONDS = 6.0` (a UI-facing request: long enough for a cold `gh` API call, short
   enough that a stalled network is not a hung view), `MAX_STDOUT_BYTES = 1_048_576`,
   `MAX_STDERR_BYTES = 8_192`.
3. Declare `ExecOutcome = Literal["ok", "not_found", "timed_out", "too_large", "failed"]` and a frozen
   `ExecResult { outcome, stdout: bytes, exit_code: int | None, stderr_marker: str | None }`.
   **`stderr_marker` is NOT stderr**: it is the name of the FIRST matching entry of a small declared
   allowlist (`STDERR_MARKERS` — not-logged-in / rate-limit / could-not-resolve-repository / HTTP-404
   / HTTP-403), matched in-memory against the capped stderr, after which the stderr bytes are
   dropped. Raw stdout/stderr is NEVER logged, NEVER put in an exception message, and NEVER returned
   to a caller other than as parsed, allowlisted data.
4. Implement `run_bounded(argv: Sequence[str], *, cwd: Path | None, timeout: float, max_stdout: int,
   env: Mapping[str, str], label: str) -> ExecResult`: `subprocess.Popen` with an explicit argv
   (**NEVER `shell=True`**), `stdin=DEVNULL`, `start_new_session=True`,
   `communicate(timeout=...)`; on `TimeoutExpired` kill the whole process GROUP (`os.killpg`) then
   reap, and return `timed_out`; `FileNotFoundError` → `not_found`; over-cap stdout → `too_large` with
   stdout dropped (never a short read — the `bounded_read` rule); non-zero exit → `failed`.
5. Implement `scrubbed_env(extra: Mapping[str, str] | None = None) -> dict[str, str]`: an ALLOWLIST of
   inherited variables (`PATH`, `HOME`, `XDG_CONFIG_HOME`, `GH_CONFIG_DIR`, `LANG`, `LC_ALL`,
   `SSH_AUTH_SOCK`) plus fixed `NO_COLOR=1`, `CLICOLOR=0`, `GH_NO_UPDATE_NOTIFIER=1`,
   `GH_PROMPT_DISABLED=1`. It deliberately **DROPS** `GH_TOKEN` / `GITHUB_TOKEN` /
   `GH_ENTERPRISE_TOKEN` / `GITHUB_ENTERPRISE_TOKEN` / `GH_REPO` / `GH_HOST`, so an ambient token can
   never act as an identity the console's own account check (T134) never validated, and an ambient
   `GH_REPO` can never redirect a query away from the repo T135 resolved. Document that.
6. Declare `CommandRunner` as a `Protocol` matching `run_bounded`'s signature, so every caller can be
   constructed with an injected runner.
7. Log ONE line per invocation at debug: the label, the executable BASENAME, the argv token count, the
   outcome and the duration — **never the argv values, never any output**. Docstring states the rule
   and cites `app.py::_announce_write_token` as the house standard for keeping a secret off the
   logging handlers.
8. CREATE `tests/unit/test_bounded_exec.py`, driving only `sys.executable -c ...` scripts: a clean
   exit; a non-zero exit; a script that sleeps past the timeout (asserting `timed_out` AND that the
   child is reaped); a script printing over the cap (`too_large`, stdout dropped); a missing
   executable (`not_found`); stderr marker classification; and that `scrubbed_env` drops each named
   token variable. Add an `assert_module_is_read_only` case via `tests/_read_only_guard.py`.

## Critical files

- `server/factory_console/github_adapter/bounded_exec.py` (create)
- `tests/unit/test_bounded_exec.py` (create)

## Interface & data

`run_bounded(argv, *, cwd, timeout, max_stdout, env, label) -> ExecResult`;
`scrubbed_env(extra=None) -> dict[str, str]`; `CommandRunner` protocol.
Constants: `DEFAULT_TIMEOUT_SECONDS=6.0`, `MAX_STDOUT_BYTES=1_048_576`, `MAX_STDERR_BYTES=8_192`,
`STDERR_MARKERS`.

Contracts referenced: ARCHITECTURE.md Cross-cutting — a subprocess is blocking I/O for the event-loop
rule, so this module is synchronous by design and its callers offload;
`file_adapter/bounded_read.py` for the ONE-sequence and never-short-read discipline (referenced, not
imported — the sequences differ: descriptor gates there, process lifetime here).

DB ops: none. NFR flags: timeout; output bound; **secret redaction** (no output ever logged or
raised); env scrub (no ambient token identity, no ambient repo target); no shell.

## Verification

`python -m pytest tests/unit/test_bounded_exec.py -q` — must complete in a few seconds, since the
timeout case uses a ~0.2 s bound, not the 6 s default; `make lint`; `python -m pytest -q`.
No route or wiring changes; `factory-console <path>` unaffected.
