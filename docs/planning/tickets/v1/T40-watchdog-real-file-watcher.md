# [T40] watchdog-backed RealFileWatcher over docs/planning/** and .factory/run-state/**

milestone: v1 · track: file-adapter · depends_on: T39, T17 · provides: RealFileWatcher — a watchdog Observer over docs/planning/** and .factory/run-state/**, debounced, thread→async fan-out, project-relative ChangeEvent paths; adds watchdog to server runtime deps. The production live-update source the backend streams via SSE.

## Context

Completes the v1 watcher: the production implementation of the `FileWatcher` port (T39) using the `watchdog` library. It observes exactly the two subtrees v1 cares about (ticket manifest/`.md` and roadmap under `docs/planning/**`, plus lane markers under `.factory/run-state/**`) and emits `ChangeEvent`s that drive the SPA's live refresh. This is the concrete piece that realizes the MVP-"no watcher"-extension flagged in T39 — an isolated, opt-in, single-process, 127.0.0.1-only component; it must never write to the target project. Because there is no v1 foundation track, this ticket also adds `watchdog` to the server runtime dependencies.

## Staged approach

1. Add `watchdog` to `server` runtime dependencies in `pyproject.toml` (the `[project].dependencies` array), then `pip install -e '.[dev]'` so it resolves.
2. Add `server/factory_console/file_adapter/watcher_real.py`: `RealFileWatcher(project_root: Path)` satisfying the `FileWatcher` Protocol. `start()`: capture the running asyncio loop (`asyncio.get_running_loop`, since start is called from the backend's async lifespan), create a `watchdog` `Observer`, and schedule a `FileSystemEventHandler` recursively on each of `{project_root/docs/planning, project_root/.factory/run-state}` that EXISTS (skip missing roots; also covers the `docs/planning/.run-state` fallback since it is under `docs/planning`). `stop()`: `observer.stop()` + `observer.join()`.
3. The handler maps a watchdog event to a `ChangeEvent`: `kind` from the event type; `scope = 'run-state'` when under `.factory/run-state` else `'planning'`; `path = Path(event.src_path).relative_to(project_root).as_posix()` (guard `ValueError` → skip).
4. Debounce/coalesce bursts: collapse repeated events for the same relative path within a short window (~150 ms) into a single `ChangeEvent` so one editor save yields one event.
5. Fan-out: keep a set of subscriber `asyncio.Queue`s; the watchdog thread delivers via `loop.call_soon_threadsafe(queue.put_nowait, event)` (thread→loop safe). `subscribe()` is an async generator registering a fresh queue and unregistering in `finally` — identical external contract to `FakeFileWatcher` so the backend swaps them transparently.
6. Docstring: reiterate this is the deliberate "no watcher" extension; opt-in and single-process; read-only.

## Critical files

- `pyproject.toml` (add watchdog to server runtime deps)
- `server/factory_console/file_adapter/watcher_real.py` (new — RealFileWatcher)

## Interface & data

- `RealFileWatcher(project_root: Path)`; `start()`/`stop()` as on the port; `subscribe() -> AsyncIterator[ChangeEvent]` (per-client queue drained as an async generator).
- Touched BY REFERENCE (do not redefine): the `FileWatcher` Protocol and `ChangeEvent` (from T39); the watched roots match the run-state fallback locations in `file_adapter/run_state.py` (`find_run_state_dir`). External dependency: `watchdog` (`Observer` + `FileSystemEventHandler`).
- DB ops: N/A. NFR: opt-in + single-process + in-process (no extra socket; the backend's SSE endpoint is the only network surface, still 127.0.0.1); read-only (guard-tested: no write/create/delete under the project, like `run_state.py`); project-relative paths only; thread-safe cross-thread delivery via `loop.call_soon_threadsafe`; debounced.

## Verification

`pytest-asyncio` integration test against a `tmp_path` project: start the `RealFileWatcher`, subscribe, then write a `docs/planning/tickets/<id>.md` and a `.factory/run-state/ready/<id>` marker and assert the corresponding `ChangeEvent`s arrive with correct `scope` and project-relative `path`; assert bursts are debounced to one event; assert a change outside the two watched roots yields nothing; assert `stop()` joins cleanly with no lingering thread. A guard test asserts the module contains no filesystem-mutating call (mirrors `run_state.py`'s read-only guard). Requires `watchdog` installed; skip/xfail cleanly if absent. Cross-checks `isinstance(RealFileWatcher(root), FileWatcher)`.
