# server/

The Python package `factory_console` (imported as `factory_console`, package root at `server/factory_console/`).

## Planned stack

- Python 3.11+
- FastAPI (Uvicorn ASGI) — HTTP handlers
- Typer — CLI entrypoint (`factory-console`)
- Pydantic v2 — domain models (source of truth for the OpenAPI schema the SPA consumes)
- pydantic-settings — config (`FACTORY_CONSOLE_HOST/PORT/LOG_LEVEL/WRITE_TOKEN`), 127.0.0.1 validator-pinned
- markdown-it-py + mdit-py-plugins + bleach — server-side markdown rendering + sanitization

## Layout (populated by MVP tickets)

- `factory_console/cli.py` — Typer entrypoint (T06 walking skeleton, T25 full extension).
- `factory_console/app.py` — `create_app(file_adapter, *, version, project_root)` factory (T06 stub, T20 rewrite).
- `factory_console/config.py`, `logging.py`, `errors.py` — cross-cutting (T04).
- `factory_console/api/v1/` — HTTP handlers (T20–T24).
- `factory_console/services/` — orchestrators calling the FileAdapter Protocol (T22–T23).
- `factory_console/domain/` — Pydantic models (T07).
- `factory_console/file_adapter/` — the only layer that reads the TARGET PROJECT's files (T10–T17). (`api/deps._probe_root`, T111, is a sanctioned narrow exception: a readability-only stat/scandir for the multi-project selection seam, not a reuse of `file_adapter`'s own probing.)
- `factory_console/store/` — the console's OWN writable DB, outside every project (v3, T104).
- `factory_console/_static/` — built SPA copied here at package time (gitignored).
