# [T137] RealGitHubAdapter — `gh pr list`, degrading honestly

milestone: v3.0.1 · track: github · depends_on: T132, T133, T134, T135, T136 · provides: `github_adapter/real.py` — the concrete `GitHubAdapter` composing account + remote + bounded exec + parse into ONE `gh` invocation per project, returning a `GitHubPullsRead` whose every failure mode is a named reason.

## Context

This is where the four collaborators become an implementation, and it is deliberately the LAST of them
so it contains orchestration and degradation mapping only — it opens nothing, parses nothing and
spawns nothing itself.

**Most of its substance is the degradation table.** The port promises TOTALITY and MONOTONICITY, and
the whole value of the feature is that `gh` missing, `gh` pinned to the wrong account, a moved working
copy, a GitLab remote, an invisible repo, a rate limit and a timeout are **seven different sentences in
the UI** rather than one empty column.

One invocation covers the whole project — the listing is joined to ticket ids afterwards — not one per
ticket. An N-ticket manifest must never mean N network calls.

## Staged approach

1. CREATE `server/factory_console/github_adapter/real.py` with the `# READ-ONLY:` banner.
2. `class RealGitHubAdapter` with `__init__(self, *, settings: GitHubSettings | None = None,
   runner: CommandRunner | None = None, account_resolver=resolve_account,
   slug_resolver=resolve_repo_slug)`. Stateless and **cacheless** (caching is v3.4 and must not appear
   here). The injected `runner`/resolvers are the seam that guarantees no test ever executes real
   `gh`; state that in the class docstring.
3. `list_pull_requests(project)`:
   - (a) `account_resolver` → on a reason, return
     `GitHubPullsRead(source=GitHubSource(found=False, read=False, reason=<reason>, repo=None,
     account=None), pullsByTicket={})`;
   - (b) `slug_resolver` → on a reason, the same shape but carrying the resolved `account`;
   - (c) with executable + account + slug in hand, `found=True`.
4. Invoke `[<gh>, "pr", "list", "--repo", slug, "--state", "all", "--limit", str(PR_LIST_LIMIT),
   "--json", ",".join(GH_REQUESTED_FIELDS)]` through `run_bounded` with `cwd=project.rootPath`,
   `timeout=settings.gh_timeout_seconds`, `env=scrubbed_env()`. `PR_LIST_LIMIT = 200` — a declared
   constant with its reasoning (`gh`'s default 30 silently truncates a real project's history; 200
   bounds the payload alongside the byte cap).
5. Map `ExecResult` → reason: `not_found` → `gh_absent` (a race after the account check's `which`);
   `timed_out` → `timed_out`; `too_large` → `too_large`; `failed` → classify by `stderr_marker` into
   `repo_not_visible` (404/403/could-not-resolve), `rate_limited`, `gh_unauthenticated`, else
   `unreadable`. **A timeout resolves to `timed_out` and NEVER to an empty list** — write that as a
   comment where it is easy to get wrong.
6. On `ok`, `parse_pull_requests` → a reason becomes that reason with `found=True, read=False`; a list
   becomes `index_by_ticket(...)` with `found=True, read=True, reason=None`.
7. **MONOTONICITY assertion in code, not just prose**: build the successful result through one private
   helper that is the ONLY constructor of `read=True`, so no degraded branch can accidentally return a
   populated `pullsByTicket`.
8. Log one line per call: outcome name, slug, duration. Never argv values, never output, never stderr.
9. CREATE `tests/unit/test_real_github_adapter.py`: one case per reason in the vocabulary, driven by
   stub resolvers and a stub runner; the happy path with a realistic payload; a timeout yielding
   `timed_out` with an EMPTY map; an assertion that the argv contains `--repo` and the exact `--json`
   field list (the explicit target and the source-side narrowing, both pinned); an assertion that no
   test constructs the adapter with the default runner; `assert_module_is_read_only`.

## Critical files

- `server/factory_console/github_adapter/real.py` (create)
- `tests/unit/test_real_github_adapter.py` (create)

## Interface & data

Implements the port: `list_pull_requests(self, project: Project) -> GitHubPullsRead`.
Constructor: `RealGitHubAdapter(*, settings=None, runner=None, account_resolver=resolve_account,
slug_resolver=resolve_repo_slug)`.

External command: `gh pr list --repo <owner/name> --state all --limit 200 --json
number,url,state,isDraft,headRefName,updatedAt` — **READ-ONLY; no `gh` subcommand that mutates GitHub
or `gh` state may ever appear in this module.** Constant: `PR_LIST_LIMIT = 200`.

Contracts referenced: the `GitHubAdapter` Protocol (TOTAL, monotone — T132); T131's reason vocabulary;
`GH_REQUESTED_FIELDS` (T136); `scrubbed_env` / `run_bounded` (T133).

DB ops: none. NFR flags: timeout (`FACTORY_CONSOLE_GH_TIMEOUT_SECONDS`, default 6 s); output bound;
account check; explicit repo target; **no cache (v3.4)**; secrets never logged; blocking — callers MUST
offload.

## Verification

`python -m pytest tests/unit/test_real_github_adapter.py -q`; `make lint`; `python -m pytest -q`.
Confirm the isolation rule still holds — importing `factory_console.app` must not pull in this module
(nothing imports the concrete yet). Grep as a review aid:
`grep -nE 'auth (switch|login|logout)|pr (merge|create|close)|repo (delete|create)'
server/factory_console/github_adapter/real.py` must print nothing.
