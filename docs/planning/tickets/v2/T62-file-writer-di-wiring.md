# [T62] Wire the FileWriter port into create_app + CLI with a get_file_writer DI seam

milestone: v2 · track: backend · depends_on: T20, T25, T60, T61 · provides: create_app accepts + stashes a FileWriter; get_file_writer Depends() seam; CLI + create_dev_app construct RealFileWriter.

## Context

The write endpoints need the write-core `FileWriter` reachable through the same DI pattern the read handlers use for `FileAdapter`, without any handler importing a concrete adapter. This ticket adds the plumbing only (no routes yet): `create_app` gains an optional `file_writer` argument stashed on `app.state.file_writer`, a companion `get_file_writer` provider is added next to `get_file_adapter`/`get_file_watcher`, and the two production construction sites (`cli.py`, `create_dev_app`) build the concrete `RealFileWriter`. It is the seam every later write ticket consumes.

## Staged approach

1. In `api/deps.py`, add `get_file_writer(request) -> FileWriter` mirroring `get_file_adapter`: read `request.app.state.file_writer`, raise `RuntimeError` if unbound/None (a write route without a wired writer is a wiring bug, exactly like the adapter seam).
2. In `app.py`, add a keyword-only `file_writer: FileWriter | None = None` param to `create_app`, stash it on `app.state.file_writer` alongside the existing state; update the docstring.
3. In `create_dev_app`, lazily import and construct `RealFileWriter()` and pass `file_writer=` into `create_app` (keep imports lazy so importing `app.py` never pulls the concrete writer/`watchdog`).
4. In `cli.py`, construct `RealFileWriter()` next to `RealFileAdapter()` and pass it into the `create_app(...)` call.
5. Do NOT re-export anything from a package `__init__`; import the port/impl by full module path.

## Critical files

- `server/factory_console/app.py`
- `server/factory_console/api/deps.py`
- `server/factory_console/cli.py`

## Interface & data

`get_file_writer(request: Request) -> FileWriter` (write-core port); `create_app(file_adapter, *, version, project_root, file_watcher=None, file_writer: FileWriter | None = None) -> FastAPI`. By reference: write-core `FileWriter` port + `RealFileWriter` impl (T60/T61; do not redefine). No DB. NFR: preserves single-worker concurrency; no auth on this seam (token added in T64). No new routes → OpenAPI unchanged.

## Verification

`pytest tests/integration` — a test that `create_app(..., file_writer=FakeFileWriter())` stashes it and `get_file_writer` returns it; assert `get_file_writer` raises `RuntimeError` when unbound. CLI subprocess smoke (existing harness) still boots and serves `/api/v1/health`. ruff + ruff-format clean. No route/behaviour change for read endpoints.
