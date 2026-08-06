# [T116] Per-request resolution for /project, /health, /graph, /roadmap

milestone: v3.0 · track: backend · depends_on: T111, T21, T24, T98 · provides: four endpoints resolved from the current selection instead of the boot-fixed root, each converted to the anyio offload as the house rule requires of a touched handler, and `/health` made honest about having no selection.

## Context

The first half of the conversion sweep, split by file so each PR stays a simple, reviewable diff.
Each handler loses its `root: Path = request.app.state.project_root` line and gains
`root: Path = Depends(get_current_project_root)`. The house rule says a handler converts as it is
next edited, and this is that edit: all four still call `adapter.load_project(root)` inline today, so
each gains the `anyio.to_thread.run_sync(partial(...))` offload here.

`/health` is the one that is NOT merely mechanical. It must never 409 — it is the endpoint an
operator (and the SPA's boot sequence) uses to find out WHY nothing else answers — so `projectRoot`
becomes nullable and a named `selectionReason` says which condition holds. **That is a breaking
narrowing** for any consumer that assumed a string: call it out for the frontend (T121) and the e2e
harness (T120). It obeys honest-missing: a console with no selection reports a named absence, not a
fabricated root. `ok` stays `true` — the process is healthy even when nothing is selected, and
conflating the two would make the SPA's boot probe report a misconfiguration as an outage.

## Staged approach

1. EDIT `server/factory_console/api/v1/project.py` — swap the state read for
   `Depends(get_current_project_root)`; offload `load_project`. Update the module + handler
   docstrings to say the root is the SELECTED project's and that the selection 409s propagate through
   the already-registered domain-error handler (no new error handling here).
2. EDIT `server/factory_console/api/v1/graph.py` — same swap; offload both the `load_project` and the
   `get_graph` call.
3. EDIT `server/factory_console/api/v1/roadmap.py` — same swap; offload.
4. EDIT `server/factory_console/api/v1/health.py` — do NOT depend on `get_current_project_root` (it
   raises); instead read `app.state.selection` + the registry through a small offloaded
   resolve-or-explain helper. `HealthResponse` becomes
   `{ ok, version, projectRoot: Path | None, selectedProjectId: str | None,
   selectionReason: SelectionFailure | None }` — the `selectionReason` values come from T111's shared
   union, not a new spelling.
5. EDIT `tests/integration/test_api_project.py`, `test_api_graph.py`, `test_api_roadmap.py` — add a
   no-selection case (`409 no_project_selected`) and a selected-path-deleted case
   (`409 selected_project_unavailable`) each; existing pinned-mode cases stay green untouched.
6. EDIT `tests/integration/test_api_health.py` — the three selection states, and that `ok` is `true`
   in all three.

## Critical files

- `server/factory_console/api/v1/project.py` (modify)
- `server/factory_console/api/v1/graph.py` (modify)
- `server/factory_console/api/v1/roadmap.py` (modify)
- `server/factory_console/api/v1/health.py` (modify)
- `tests/integration/test_api_project.py` (modify)
- `tests/integration/test_api_health.py` (modify)

## Interface & data

Unchanged response shapes: `GET /project` → `Project`; `GET /graph` → `TicketGraph`;
`GET /roadmap` → `Roadmap | { present: false }` — all exactly as ARCHITECTURE.md → REST v1 declares
them. Only WHICH project they describe changes.

**Changed:** `GET /health` →
`{ ok: boolean, version: string, projectRoot: string|null, selectedProjectId: string|null,
selectionReason: "no_selection"|"selected_project_not_registered"|"selected_project_missing"|"selected_project_unreadable"|null }`.
`projectRoot` becoming nullable is a breaking narrowing — flagged for T120 and T121.

New error responses on the three project-scoped routes: `409 no_project_selected`,
`409 selected_project_unavailable` (envelope `{ error: { code, message } }`, message names the path).

Contracts by reference: `FileAdapter.load_project` / `.get_graph` / `.get_roadmap` (ARCHITECTURE.md →
FileAdapter port); `Project` and `Roadmap` (Data model); T111's `SelectionFailure` union. Nothing is
redefined.

DB ops: an indirect registry `SELECT` per request through the resolution seam, already offloaded
inside the dependency. NFR flags: anyio offload added at each of the four handler boundaries
(Concurrency: applied per endpoint as it is touched); no auth change; no cache.

## Verification

`python -m pytest tests/integration/test_api_project.py tests/integration/test_api_graph.py
tests/integration/test_api_roadmap.py tests/integration/test_api_health.py -q`, then
`python -m pytest -q`. `make lint`. Manual:
`factory-console tests/fixtures/projects/minimal` and confirm all four answer as before in pinned
mode.
