# [T20] Rewrite app factory: create_app(file_adapter, *, version, project_root) + DI seam + error handler + create_dev_app for scripts/dev.sh

milestone: MVP · track: backend · depends_on: T04, T06, T09, T10, T11, T12, T13, T14, T15, T17 · provides: `create_app(file_adapter: FileAdapter, *, version: str, project_root: Path)` + `create_dev_app()` zero-arg dev factory (referenced by `scripts/dev.sh`) + `get_file_adapter` Depends + `FactoryConsoleError` exception handler + `RequestValidationError` handler (with `invalid_ticket_id` special case) + access-log middleware

## Context

Replaces T06's walking-skeleton `create_app` with the real app factory the CLI (T25) will boot and that all downstream endpoint tickets extend. Stands up the two cross-cutting seams every subsequent endpoint depends on: DI wiring (`Depends(get_file_adapter)`) so handlers get a `FileAdapter` without ever importing `real.py`, and a single error mapper that catches `FactoryConsoleError` (raised by file-adapter modules per synthesis note) and returns the `{ error: { code, message, details? } }` envelope. Wires the request-log middleware from T04's `logging.py`. Also ships `create_dev_app()` — a zero-arg wrapper the `uvicorn --factory` invocation in `scripts/dev.sh` depends on — so `make dev` keeps working after this rewrite (T09's `dev.sh` currently boots a zero-arg `create_app`; rewriting `create_app` to require a `file_adapter` would break `dev.sh` without this wrapper). Depends on T17 because `create_dev_app` instantiates `RealFileAdapter`; T25 (CLI) is still the ONLY runtime user of `RealFileAdapter` for production launches, but the dev-loop shortcut needs it too. First real backend ticket; every other backend ticket depends on it.

## Staged approach

1. `server/factory_console/api/__init__.py` + `api/deps.py` exporting `get_file_adapter() -> FileAdapter` reading `app.state.file_adapter` (raise `RuntimeError` if unbound).
2. Extend `server/factory_console/errors.py` or add a small `api/error_handlers.py` registering:
   - (a) `FactoryConsoleError -> JSONResponse(status_code=exc.status, content=to_error_response(exc))` — this ONE handler covers `ProjectNotFound / MalformedManifest / PathTraversal / TicketFileMissing / TicketNotFound` transparently.
   - (b) `RequestValidationError -> envelope with code='validation_error'`, `details=errors[]`, status 422 BUT with a special-case: if any error entry's `loc` ends in a field with the `TICKET_ID_PATTERN` constraint (specifically `Path` params named `ticket_id`) AND the error type is a pattern-mismatch, return `{ status: 400, code: 'invalid_ticket_id', message: 'Ticket id must match ^[A-Za-z0-9_.-]+$' }` instead — so an invalid ticket id yields the same envelope whether validation fails at the FastAPI `Path` boundary (T22) or deeper in T13's `_safe_resolve`.
3. Rewrite `server/factory_console/app.py`: `create_app(file_adapter: FileAdapter, *, version: str, project_root: Path) -> FastAPI` (`project_root` is NON-optional — the CLI always discovers a root before boot; tests always pass a fixture root). Instantiate `FastAPI(title, version, openapi_url='/api/v1/openapi.json', docs_url=None, redoc_url=None)`; stash `file_adapter + project_root + version` on `app.state`; register both exception handlers; add `BaseHTTPMiddleware` subclass that times the request and emits one access-log line via `logging.getLogger('factory_console.access')`; include `api/v1` router (empty for now — subsequent tickets add sub-routers). Keep T06's `/api/v1/health` mounted temporarily so foundation smoke keeps passing (T24 relocates it).
4. ADD `create_dev_app() -> FastAPI` in `app.py`: `def create_dev_app(): from .file_adapter.discovery import discover_project; from .file_adapter.real import RealFileAdapter; from . import __version__; root = discover_project(None, Path.cwd()); return create_app(RealFileAdapter(), version=__version__, project_root=root)`. This is what `scripts/dev.sh`'s `uvicorn --factory` invocation targets. Update `scripts/dev.sh` to `uvicorn factory_console.app:create_dev_app --factory --reload --port $PY_PORT --host 127.0.0.1`.
5. Do NOT modify `cli.py` in this ticket — T25 owns the full CLI extension including `RealFileAdapter` wiring. Between T20 and T25, `factory-console --no-browser --port N` will not start (foundation smoke test therefore adapts to use `python -c 'from factory_console.app import create_dev_app; import uvicorn; uvicorn.run(create_dev_app(), ...)'` OR pytest-level `create_app(FakeFileAdapter(), version='0.0.0', project_root=fixture_root)` — T20's smoke-test update goes in `ci.yml` only if that job existed pre-T20; otherwise CI's smoke step becomes conditional on T25 having landed). Simpler: mark that after T20 lands and before T25 lands, `make smoke` may be skipped — this window is short (one PR).
6. `tests/unit/test_error_mapper.py`: raise each exception subtype in a bare FastAPI TestClient route; assert envelope + status; separate test asserts an invalid ticket-id `Path` param returns `{ status: 400, code: 'invalid_ticket_id' }` (not 422).
7. `tests/integration/test_app_factory.py`: over `create_app(FakeFileAdapter(), version='0.0.0', project_root=Path('/tmp/fake-root'))`: `/openapi.json` returns 200 with valid schema; unhandled `ProjectNotFound` on a probe route -> 404 envelope; unrelated `RequestValidationError` (missing body field) -> 422 `code='validation_error'`; ticket-id pattern violation -> 400 `code='invalid_ticket_id'`; one access-log line per request via `caplog`.

## Critical files

- `server/factory_console/app.py`
- `server/factory_console/api/__init__.py`
- `server/factory_console/api/deps.py`
- `server/factory_console/errors.py`
- `scripts/dev.sh`
- `tests/unit/test_error_mapper.py`
- `tests/integration/test_app_factory.py`

## Interface & data

Public seams: `create_app(file_adapter, *, version, project_root: Path) -> FastAPI`; `create_dev_app() -> FastAPI`; `get_file_adapter() -> FileAdapter`. Consumes REST v1 error envelope shape. Consumes `FileAdapter` Protocol (`real.py` imported ONLY inside `create_dev_app` + T25). Access-log format matches T04. `Path` constraint `TICKET_ID_PATTERN` handled uniformly regardless of validation depth (single error envelope for the SPA).

## Verification

pytest passes both new test files; `make dev` still boots (dev.sh + `create_dev_app` wired); ruff clean; `python -c "from factory_console.app import create_app; from factory_console.file_adapter.fake import FakeFileAdapter; from pathlib import Path; print([r.path for r in create_app(FakeFileAdapter(), version='0.0.0', project_root=Path('/')).routes])"` includes `/api/v1/health` + `/api/v1/openapi.json`.
