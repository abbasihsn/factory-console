# [T06] Walking-skeleton FastAPI app + trivial Typer CLI + /api/v1/health

milestone: MVP · track: foundation · depends_on: T02, T04 · provides: Bootable factory-console CLI + minimal FastAPI app exposing `GET /api/v1/health -> { ok: true, version, projectRoot: null }` — the spine every other track wires into

## Context

Deliberately trivial walking-skeleton. Gives backend an app to extend, gives CI a smoke target, gives frontend a `/health` endpoint to hit during dev. No path discovery yet (`projectRoot=null`); no FileAdapter dep; no real port selection (accepts `--port 0`). Backend T20 REWRITES `create_app` to take a `file_adapter` arg + register routers + exception handlers + middleware; backend T25 replaces the trivial CLI with real discovery/port/browser/signals. Backend T24 moves `/health` into `api/v1/health.py`.

## Staged approach

1. `server/factory_console/app.py`: `def create_app() -> FastAPI` returning `FastAPI(title='Factory Console', version=factory_console.__version__, openapi_url='/api/v1/openapi.json', docs_url='/api/v1/docs')`; include a v1 router mounted at `/api/v1` with a single `GET /health` handler returning `{'ok': True, 'version': factory_console.__version__, 'projectRoot': None}`; wire static-file serving from `importlib.resources` locating `factory_console/_static/` if present (skip mount if absent — dev mode).
2. `server/factory_console/cli.py`: `app = typer.Typer(add_completion=False)`; `@app.command() def main(path: Optional[Path]=typer.Argument(None), port: int=typer.Option(0, '--port'), host: str=typer.Option('127.0.0.1', '--host'), no_browser: bool=typer.Option(False, '--no-browser'), log_level: str=typer.Option('INFO', '--log-level'), version: bool=typer.Option(False, '--version'))`; if `version -> print __version__ + typer.Exit(0)`; `configure_logging(log_level)`; `uvicorn.run(create_app(), host=host, port=port, log_level=log_level.lower())`. Docstring notes "real path discovery + port handling + browser + exit codes live in backend T25".
3. `tests/integration/test_health.py` using `httpx.AsyncClient(app=create_app())` asserting 200 + shape.

## Critical files

- `server/factory_console/app.py`
- `server/factory_console/cli.py`
- `tests/integration/test_health.py`

## Interface & data

Implements REST v1 `/health` subset (`projectRoot=null` placeholder — T24 completes). Implements CLI contract flag surface (`PATH`, `--port`, `--host`, `--no-browser`, `--log-level`, `--version`); exit codes stubbed to 0 for now (T25 completes). Consumes `configure_logging` from T04.

## Verification

`factory-console --version` prints `0.1.0` exit 0; `factory-console --no-browser --port 8765` boots; `curl http://127.0.0.1:8765/api/v1/health` returns the expected JSON; `curl http://127.0.0.1:8765/api/v1/openapi.json` returns an OpenAPI 3 doc listing `/health`; `pytest tests/integration/test_health.py -q` green.
