# [T139] GET /api/v1/github/pulls + GitHubAdapter DI wiring

milestone: v3.0.1 · track: backend · depends_on: T138, T137, T119, T116, T20, T25, T98, T102 · provides: the endpoint the SPA consumes — `{ source, items, total }` with one entry per manifest ticket, joinable per ticket id against the artifact-sourced `pr_url` on `GET /api/v1/runs` — plus the `get_github_adapter` seam and `create_app` / `create_dev_app` / CLI wiring.

## Context

This is the slice the SPA consumes. It publishes the port through the same DI shape every other port
uses and adds one route.

**The route is SEPARATE from `/runs` on purpose.** `/runs` answers "what did the factory's own
artifacts record"; this answers "what does GitHub say right now". They are the same subject read from
two sources, and putting the second onto the first's records would create two competing answers inside
one payload — the defect class v2.2 was filed against. Keeping them apart also keeps a 1–6 s network
call off the route the Runs table already blocks on: the SPA fetches this separately and joins per
ticket id (T140/T141).

**`get_github_adapter` RAISES when unbound** — deliberately the `get_run_artifact_reader` posture, not
the opt-in `get_file_watcher` one, and for that provider's exact reason: the port is TOTAL, so "gh is
not installed" already has a named answer (`gh_absent`), leaving nothing for a `None` adapter to mean
except reporting the console's own wiring as a fact about GitHub.

**FLAGGED TRADE-OFF:** one `gh` invocation per request, no caching (explicitly v3.4). Acceptable
because it is one invocation for the whole project, off the event loop, on a route nothing else waits
for — and because T140 fetches it client-side, outside the `invalidateAll()` refresh path, so an
active factory run cannot turn every file-change event into a fresh subprocess. If the UI later needs
it faster, the answer is the v3.4 cache, not a per-ticket call.

## Staged approach

1. EDIT `server/factory_console/api/deps.py`: add `get_github_adapter(request) -> GitHubAdapter`
   reading `request.app.state.github_adapter`, RAISING `RuntimeError` when unbound. Put the
   raise-vs-None reasoning in the docstring and in the module docstring's provider list, beside the
   existing five.
2. EDIT `server/factory_console/app.py`: add `github_adapter: GitHubAdapter | None = None` to
   `create_app`, stash on `app.state.github_adapter`, document it beside the other optional
   collaborators; **import the PROTOCOL only at module level**. In `create_dev_app`, LAZILY import and
   wire `RealGitHubAdapter()` alongside the other concretes, preserving the rule that importing
   `app.py` never pulls in a concrete adapter.
3. EDIT `server/factory_console/cli.py`: wire `RealGitHubAdapter()` at the existing `create_app(...)`
   call (lazy import, matching the other concretes). No new CLI flag, no new subcommand —
   `factory-console PATH` keeps its exact invocation shape (T119). A construction failure must degrade
   to `None` rather than fail the boot: `gh` being absent is the common case on a fresh machine.
4. CREATE `server/factory_console/api/v1/github.py`: `router = APIRouter(tags=["github"])`; response
   envelope `GitHubPullsResponse` (frozen, `extra="forbid"`) with `source: GitHubSource`,
   `items: list[GitHubPullEntry]`, `total: int` — the `{items, total}` envelope its sibling list
   endpoints use, plus the `source` block `/spend` established.
   **The DOMAIN models go on the wire directly (no twin models):** unlike a `.factory/` artifact the
   reading path here is typed end to end, narrowed at the source by `--json` and at construction by
   `PullRequestRef`, so there is no untyped payload to project. That is the `/spend` position, and the
   module docstring must argue it explicitly so a reader does not mistake it for an oversight against
   the `/runs` precedent.
   Handler `async def list_pull_requests(...)` depends on `get_current_project_root` (T111's seam,
   which T116 established), `get_file_adapter` and `get_github_adapter`, offloading BOTH
   `load_project` and `GitHubPullService(...).list_pull_entries` via
   `await anyio.to_thread.run_sync(partial(...))` — the house rule, doubly load-bearing here because
   the blocking work is a subprocess on a network call.
5. The module docstring states the PRECEDENCE contract verbatim for the SPA: entries are keyed by the
   same `ticketId` as `/runs`; a `pullRequest` supersedes the artifact `pr_url` for display; **any
   `reason` (including `no_pull_request`) means fall back to the artifact value LABELLED as
   artifact-sourced**; neither → render the named reason. This endpoint neither reads nor rewrites
   `/runs`' artifact data.
6. EDIT `server/factory_console/api/v1/__init__.py` to include the new router. **AGGREGATION FILE** —
   declared here and in T112; this ticket depends transitively on T112 via T119, so they serialize.
7. CREATE `tests/integration/test_api_github_pulls.py` (httpx.AsyncClient, `FakeFileAdapter` +
   `FakeGitHubAdapter`): the happy-path shape and field names; an unreadable source giving 200 with the
   reason on the source block AND on every entry (never 404, never `[]`); a project with no manifest
   tickets giving `items: []` with an intact source block; ordering matches manifest order; the
   response carries no field outside the declared models; a missing adapter raises the wiring error;
   `409 no_project_selected` with no selection.
8. EDIT `tests/integration/test_disclosure_policy.py`'s app builder to pass
   `github_adapter=FakeGitHubAdapter()` so the new schemas are reachable by the generic sweep, and
   confirm it passes **with no new entry in the free-form allowlist** — that is the point: nothing here
   is free-form. EDIT `tests/integration/test_app_factory.py` for the new `create_app` parameter and
   `app.state` binding.

## Critical files

- `server/factory_console/api/v1/github.py` (create)
- `server/factory_console/api/v1/__init__.py` (modify — aggregation file)
- `server/factory_console/api/deps.py` (modify — aggregation file)
- `server/factory_console/app.py` (modify — aggregation file)
- `server/factory_console/cli.py` (modify — aggregation file)
- `tests/integration/test_api_github_pulls.py` (create)
- `tests/integration/test_disclosure_policy.py` (modify)
- `tests/integration/test_app_factory.py` (modify)

## Interface & data

`GET /api/v1/github/pulls` → `200 GitHubPullsResponse`:

```
{ source: { found: bool, read: bool, reason: GitHubSourceReason|null,
            repo: "owner/name"|null, account: string|null },
  items: [ { ticketId: string,
             pullRequest: { number, url, state, isDraft, headRefName, updatedAt } | null,
             reason: GitHubEntryReason|null } ],   // exactly one of the two set
  total: number }
```

No query parameters, no pagination (the list is the manifest's length — the `/runs` argument).
Errors: `409 no_project_selected` / `409 selected_project_unavailable` from T111's seam;
`ProjectNotFound` / `MalformedManifest` propagate through the registered domain-error handler.
**GitHub-side failures are NEVER errors** — they are a `source.reason` at `200`.

DI: `get_github_adapter(request) -> GitHubAdapter` (raises when unbound);
`create_app(..., github_adapter: GitHubAdapter | None = None)`; `app.state.github_adapter`.

Contracts referenced, not redesigned: ARCHITECTURE.md "REST v1" (envelope, error shape, camelCase),
Cross-cutting Concurrency (anyio offload — mandatory here), "Other factory artefacts" (the disclosure
rule — satisfied structurally, no allowlist exemption), and `GET /api/v1/runs`'s `pr_url` (JOINED per
`ticketId`, never superseded as a source).

DB ops: a registry `SELECT` per request via the resolution seam. NFR flags: blocking subprocess
offloaded to a worker thread; no auth change (a read route, so no write token); **no caching (v3.4)**;
no rate-limiting of our own beyond the per-invocation timeout.

## Verification

`python -m pytest tests/integration/test_api_github_pulls.py
tests/integration/test_disclosure_policy.py tests/integration/test_app_factory.py -q`;
`python -m pytest -q` (85% gate); `make lint`.
End to end on this very repo, which IS a GitHub project: `factory-console .` then
`curl -s localhost:<port>/api/v1/github/pulls` — expect a populated `source` block; with `gh` removed
from `PATH`, expect `read: false, reason: "gh_absent"` and a reason on every entry — honest
degradation demonstrated rather than asserted. Also confirm `/api/v1/openapi.json` publishes
`GitHubPullEntry` / `PullRequestRef` so the SPA can regenerate types.
