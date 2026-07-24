# [T44] Wire the FileWatcher lifecycle into create_app (inject + lifespan + DI)

milestone: v1 · track: backend · depends_on: T39, T40, T20, T25 · provides: an injected FileWatcher started/stopped by the app lifespan and exposed via get_file_watcher — the watcher backbone for the SSE endpoint

## Context

v1 introduces the file watcher — the MVP was explicitly no-watcher (ARCHITECTURE "One-line"), so this is a DELIBERATE architecture extension, called out here. This ticket adds only the lifecycle/DI plumbing (not the SSE endpoint): `create_app` accepts an optional `FileWatcher` (T39's port) and a FastAPI lifespan starts it at boot / stops it on shutdown; a `get_file_watcher` DI provider exposes it; the two concrete composition roots (`create_dev_app` + the CLI) construct the real watchdog watcher (T40). Keeping the watcher injected (like `file_adapter`) means `create_app` and the integration tests stay watcher-free, and the CLI's existing SIGINT/SIGTERM drain gets a clean `stop()` for free.

## Staged approach

1. In `app.py`: add an `file_watcher: FileWatcher | None = None` parameter to `create_app`, stash it on `app.state.file_watcher`, and register a FastAPI lifespan (`contextlib.asynccontextmanager` passed as `lifespan=` to `FastAPI(...)`) that, when `file_watcher` is not `None`, awaits `file_watcher.start()` on entry and `file_watcher.stop()` on exit.
2. In `create_dev_app`: lazily import and construct the concrete `RealFileWatcher` (T40) rooted at the discovered root and pass it as `file_watcher` (keeping the lazy-import rule so importing `app.py` never pulls concrete file-adapter code).
3. In `cli.py`: construct the same concrete watcher rooted at the resolved root and pass it into `create_app`, so production launch runs the watcher; rely on the lifespan (driven by uvicorn's graceful shutdown) for stop.
4. Add `get_file_watcher(request) -> FileWatcher | None` to `api/deps.py`, reading `app.state.file_watcher` (returning `None` when unset, so downstream degrades gracefully), and re-export it from `api/__init__.py` alongside `get_file_adapter`.

## Critical files

- `server/factory_console/app.py` (create_app param + lifespan)
- `server/factory_console/api/deps.py` (get_file_watcher)
- `server/factory_console/api/__init__.py` (re-export)
- `server/factory_console/cli.py` (construct + inject the concrete watcher)

## Interface & data

- `create_app` signature gains `file_watcher: FileWatcher | None = None` (`FileWatcher` is the T39 port, referenced not redefined; contract: async `start()`/`stop()` + async `subscribe()` yielding `ChangeEvent`, watching `Project.rootPath`). New DI: `get_file_watcher(request) -> FileWatcher | None` off `app.state.file_watcher`. Composition roots (`create_dev_app`, `cli.py`) instantiate the concrete `RealFileWatcher` (T40, lazy import, mirroring `RealFileAdapter`).
- No JSON contract/DB touched. NFR: v1 DELIBERATE architecture extension (first watcher; MVP had none) — still single-process, 127.0.0.1, watches only the local project tree; lifespan guarantees no thread/observer leak on the CLI's SIGINT/SIGTERM drain.

## Verification

`pytest`: `create_app(FakeFileAdapter, file_watcher=<fake FileWatcher spy>)` then drive the app through an ASGI lifespan (`httpx ASGITransport` / `TestClient` context) and assert `start()` ran on startup and `stop()` on shutdown; `create_app` with `file_watcher=None` starts cleanly and `get_file_watcher` returns `None`. Unit-assert `get_file_watcher` reads `app.state`. CLI smoke: launch `factory-console` on a fixture, hit `/health`, SIGINT, assert exit 0 (watcher stops cleanly, no hang) — extends the existing T25 CLI subprocess test.
