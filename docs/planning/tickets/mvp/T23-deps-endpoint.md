# [T23] Deps endpoint (GET /api/v1/tickets/{id}/deps) + DepsService

milestone: MVP · track: backend · depends_on: T22 · provides: `GET /api/v1/tickets/{id}/deps` returning `DepNeighborhood`

## Context

Powers the SPA dep-neighborhood view. Pure per-request derivation from `FileAdapter.get_deps` (delegating when available; falling back to `list+get+read_run_state` composition).

## Staged approach

1. `services/deps_service.py`: `class DepsService(adapter: FileAdapter): get_neighborhood(project, ticket_id) -> DepNeighborhood`. Delegate to `adapter.get_deps` if present; if returns `None` raise `TicketNotFound` (reuse T22-owned exception); else fallback path (fetch target; if `None -> TicketNotFound`; fetch all; build index; `directDeps` by lookup; `directDependents` by reverse scan; `unresolvedDeps` for missing ids).
2. Add `GET /tickets/{ticket_id}/deps` handler in `api/v1/tickets.py` (same module — related resource) using `TICKET_ID_PATTERN`.
3. `tests/unit/test_deps_service.py` against `FakeFileAdapter`: happy path with resolved deps + dependents; unresolved dep string; no-deps ticket; unknown id -> `TicketNotFound`.
4. `tests/integration/test_api_deps.py` against `RealFileAdapter` over `with_run_state` fixture: happy shape; 404 for unknown id; 400 `code=invalid_ticket_id` for invalid id; `unresolvedDeps` populated when fixture includes a dangling reference.

## Critical files

- `server/factory_console/services/deps_service.py`
- `server/factory_console/api/v1/tickets.py`
- `tests/unit/test_deps_service.py`
- `tests/integration/test_api_deps.py`

## Interface & data

Implements REST v1 `/api/v1/tickets/{id}/deps -> DepNeighborhood`. Uses `FileAdapter.get_deps` (or fallback composition). Errors: `TicketNotFound -> 404`; invalid id -> 400.

## Verification

pytest new files green; manual `curl .../deps` shows resolved + dependents + unresolved arrays; ruff clean.
