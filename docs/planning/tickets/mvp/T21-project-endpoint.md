# [T21] Project endpoint (GET /api/v1/project) + OpenAPI publish

milestone: MVP · track: backend · depends_on: T20 · provides: `GET /api/v1/project` + OpenAPI v1 schema at `/api/v1/openapi.json` becomes non-trivial (first real endpoint frozen for frontend codegen)

## Context

First real (non-health) endpoint. Returns the discovered `Project` entity so SPA can render project header. Landing this freezes OpenAPI enough for frontend to run `openapi-typescript`, unblocking frontend parallelism. Straight `FileAdapter.load_project(root)` call. Relies on T20's invariant that `app.state.project_root` is always a valid `Path` (`create_app` requires it, the CLI always discovers a root before boot).

## Staged approach

1. `server/factory_console/api/v1/__init__.py` exports `router = APIRouter(prefix='/api/v1')`.
2. `api/v1/project.py`: `async def get_project(request: Request, adapter: FileAdapter = Depends(get_file_adapter)) -> Project` — reads `app.state.project_root` (guaranteed `Path` per T20), calls `adapter.load_project(root)`, returns `Project`.
3. Include v1 project router in `create_app`.
4. `tests/integration/test_api_project.py` over `create_app(FakeFileAdapter(with_project=Project(...)), version='0.0.0', project_root=fixture_root)`: assert 200 + `Project` shape; `/api/v1/openapi.json` includes `/api/v1/project` path with `Project` schema.

## Critical files

- `server/factory_console/api/v1/__init__.py`
- `server/factory_console/api/v1/project.py`
- `server/factory_console/app.py`
- `tests/integration/test_api_project.py`

## Interface & data

Implements REST v1 `GET /api/v1/project -> Project` (per data_model). Uses `FileAdapter.load_project(root)`. Errors: `ProjectNotFound -> 404` via T20's mapper.

## Verification

`pytest test_api_project.py` green; manual `curl` returns JSON `Project`; `curl .../openapi.json | grep '/api/v1/project'` finds path; ruff clean.
