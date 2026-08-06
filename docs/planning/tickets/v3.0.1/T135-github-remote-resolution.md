# [T135] Project GitHub remote resolution — the WHERE check

milestone: v3.0.1 · track: github · depends_on: T131, T133 · provides: `github_adapter/remote.py` — resolves a registered project's `owner/name` GitHub slug from its `origin` remote, distinguishing `git_absent` / `not_a_git_repo` / `no_remote` / `not_github` / `timed_out` / `unreadable` as separate, named answers.

## Context

The adapter must always name the repo it asks about (`--repo owner/name`) rather than trusting the
process's working directory — the explicit-target posture, and the only way each failure can be told
apart. `gh`'s own stderr cannot do that job: a project that is not a git repo, one with no remote, and
one whose remote is GitLab are three DIFFERENT answers an operator acts on differently, and folding
them into one "gh failed" would be exactly the named-missing violation this track exists to avoid.

A registered project's working copy may also have been **moved or deleted since it was registered** —
a v3.0-specific case the single-project console never had — and that must resolve to
`not_a_git_repo`, never to an empty PR list.

This module owns the WHERE axis entirely; T134 owns WHO.

## Staged approach

1. CREATE `server/factory_console/github_adapter/remote.py` with the `# READ-ONLY:` banner.
2. `resolve_repo_slug(project: Project, *, runner: CommandRunner, timeout: float = 2.0) -> str |
   GitHubSourceReason`. A short 2 s bound, separate from the `gh` bound: this is a local command, and
   paying the full network timeout twice would double the endpoint's worst case.
3. **Short-circuit BEFORE spawning**: if `project.rootPath` does not exist or is not a directory,
   answer `not_a_git_repo` without a subprocess (the moved-project case).
4. Run `git -C <project.rootPath> remote get-url origin` through `run_bounded` with `scrubbed_env()`.
   Map: `not_found` → `git_absent`; `timed_out` → `timed_out`; `too_large` → `unreadable`; `failed` →
   `not_a_git_repo` when the marker/exit says the path is not a repository, `no_remote` when it says
   the remote does not exist, else `unreadable`.
5. `parse_github_slug(url: str) -> str | None`: accept `https://github.com/owner/repo(.git)`,
   `http://…`, `git@github.com:owner/repo(.git)`, `ssh://git@github.com/owner/repo(.git)`, and
   `github.com:owner/repo`; strip a trailing `.git` and surrounding whitespace; validate both segments
   against `^[A-Za-z0-9_.-]+$`. Anything else — **including a GitHub Enterprise host** — returns
   `None` → `not_github`. Document the Enterprise limitation as accepted for v3.0.1 rather than
   silently mis-answering.
6. **Never log the remote URL** — it can carry a userinfo credential in
   `https://user:token@github.com/...` form. Log the outcome name and, on success, the SLUG only.
   Strip any userinfo before parsing, and treat a URL that carried one as still parseable but never
   quotable.
7. CREATE `tests/unit/test_github_remote.py`: table-drive `parse_github_slug` over every accepted
   spelling plus GitLab, Enterprise, a bare path, and a userinfo URL (asserting the credential never
   appears in `caplog`); drive `resolve_repo_slug` with a stub runner for each `ExecOutcome`; add the
   nonexistent-root short-circuit case and `assert_module_is_read_only`. No real `git`, no network.

## Critical files

- `server/factory_console/github_adapter/remote.py` (create)
- `tests/unit/test_github_remote.py` (create)

## Interface & data

`resolve_repo_slug(project: Project, *, runner: CommandRunner, timeout: float = 2.0) -> str |
GitHubSourceReason`; `parse_github_slug(url: str) -> str | None`.

Entities by reference: `domain/project.py::Project` (only `rootPath` is used), the reason `Literal`
from T131. Subprocess: `git -C <root> remote get-url origin`, argv-only, 2 s bound, scrubbed env
(T133).

DB ops: none. NFR flags: timeout; **secret handling** — a userinfo credential in a remote URL is never
logged; explicit-target resolution so the adapter never falls through to cwd-trust; TOTAL — never
raises.

## Verification

`python -m pytest tests/unit/test_github_remote.py -q`; `make lint`; `python -m pytest -q`.
Nothing is wired yet, so `factory-console <path>` is unaffected.
