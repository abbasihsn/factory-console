# [T24] Roadmap endpoint + relocated + enriched health handler

milestone: MVP · track: backend · depends_on: T20, T21 · provides: `GET /api/v1/roadmap` (presence-only) + `GET /api/v1/health` enriched with `projectRoot`; walking-skeleton `/health` moved out of `app.py` into `api/v1/health.py`

## Context

Rounds out the MVP REST surface. `/roadmap` returns presence-only in MVP (`{ present: true, path } | { present: false }`); full body is v1. `/health`, currently inline in `app.py` from T06, gets moved to its own module and enriched to include resolved `projectRoot` from `app.state`.

## Staged approach

1. `api/v1/roadmap.py`: `async def get_roadmap(request, adapter=Depends(get_file_adapter)) -> Roadmap | RoadmapAbsent` — reads `app.state.project_root`, calls `adapter.get_roadmap(project)`, returns `{ present: true, path: roadmap.path }` if present else `{ present: false }`.
2. `api/v1/health.py`: `async def get_health(request) -> HealthResponse` — reads `app.state.version + project_root`; returns `{ ok: True, version, projectRoot: str(root) if root else None }`.
3. Include both routers in `create_app`; remove inline walking-skeleton `/health` from `app.py`.
4. `tests/integration/test_api_roadmap.py` over `RealFileAdapter+minimal` (absent) and `+with_run_state` (present) — assert both branches.
5. `tests/integration/test_api_health.py` asserts `/health` includes `projectRoot` pointing at fixture root, and `{ projectRoot: null }` when unbound.

## Critical files

- `server/factory_console/api/v1/roadmap.py`
- `server/factory_console/api/v1/health.py`
- `server/factory_console/app.py`
- `tests/integration/test_api_roadmap.py`
- `tests/integration/test_api_health.py`

## Interface & data

Implements REST v1 `/api/v1/roadmap` + `/api/v1/health` per contract. Uses `FileAdapter.get_roadmap + load_project`.

## Verification

pytest new files green; against `minimal` fixture `curl /roadmap` -> `{ present: false }`; against `with_run_state` -> `{ present: true, path: ... }`; `curl /health` includes `projectRoot`; ruff clean.
