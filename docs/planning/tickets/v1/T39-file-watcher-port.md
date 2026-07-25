# [T39] FileWatcher port + ChangeEvent model + deterministic FakeFileWatcher

milestone: v1 · track: file-adapter · depends_on: T07, T10 · provides: a NEW FileWatcher port (start/stop/subscribe → AsyncIterator[ChangeEvent]) + ChangeEvent domain model + a deterministic, test-drivable FakeFileWatcher — unblocks the backend SSE endpoint before any watchdog wiring exists

## Context

v1 introduces live updates via an SSE `/api/v1/events` endpoint (T45) fed by a file watcher. This is the ONE deliberate break from the MVP's explicit "no database, no cache, NO watcher" rule (ARCHITECTURE "## One-line" + "## Cross-cutting"): a long-lived component now runs alongside the otherwise stateless per-request adapter. To keep that stateless-adapter invariant intact, the watcher is a SEPARATE port — NOT a method on the per-request `FileAdapter`. This ticket ships the port contract + a deterministic fake so the backend can build and test the SSE endpoint (mirroring how T10's protocol+fake unblocked the backend early), before the watchdog-backed implementation (T40) lands.

## Staged approach

1. Add `server/factory_console/domain/watch.py`: a frozen Pydantic `ChangeEvent` model `{ kind: Literal['created','modified','deleted','moved'], path: str, scope: Literal['planning','run-state'], at: datetime }`. `path` is ALWAYS project-relative (never absolute) — a security note in the docstring. Import by full path (`from factory_console.domain.watch import ChangeEvent`); do NOT re-export via `domain/__init__.py` (SSE events are streamed, not part of the request `response_model` set).
2. Add `server/factory_console/file_adapter/watcher.py`: a `@runtime_checkable` `FileWatcher` Protocol with `start() -> None` (idempotent begin), `stop() -> None` (halt + release), and `subscribe() -> AsyncIterator[ChangeEvent]` (each call registers a fresh per-client stream).
3. In the same file add `FakeFileWatcher`: keeps a list of `asyncio.Queue` subscribers; `start()`/`stop()` flip a flag; a test-only `emit(event: ChangeEvent)` fans the event out to every subscriber; `subscribe()` is an async generator that creates+registers a queue, yields awaited events, and unregisters in a `finally` (client-disconnect / cancellation safe). Deterministic: no threads, no clock, no FS — tests `emit()` then assert receipt.
4. Docstring-flag this module as the deliberate architecture extension and note the `RealFileWatcher` lands in T40.

## Critical files

- `server/factory_console/domain/watch.py` (new — ChangeEvent)
- `server/factory_console/file_adapter/watcher.py` (new — FileWatcher Protocol + FakeFileWatcher)

## Interface & data

- `FileWatcher.start() -> None`; `FileWatcher.stop() -> None`; `FileWatcher.subscribe() -> AsyncIterator[ChangeEvent]` (async generator, one independent stream per call); `FakeFileWatcher.emit(event) -> None` (test driver, not on the Protocol).
- Contracts: this DEFINES a new port (`FileWatcher`) parallel to — and deliberately independent of — the `FileAdapter` Protocol; references `ChangeEvent` only.
- New model `ChangeEvent { kind, path (project-relative), scope, at }` (frozen, `extra='forbid'`, camelCase; JSON-serializable for SSE).
- DB ops: N/A. NFR: opt-in (constructed/started only when the backend enables live updates); single-process, in-process; read-only (a watcher never mutates the project); project-relative paths only (no filesystem-layout disclosure); deterministic fake (no threads/clock).

## Verification

`pytest-asyncio` tests for `FakeFileWatcher`: `subscribe()` before/after `emit`; a single `emit` fans out to two concurrent subscribers; a cancelled/closed subscriber is unregistered (no leak) and doesn't block others; `start()`/`stop()` are idempotent. Assert `isinstance(FakeFileWatcher(), FileWatcher)` holds via the runtime-checkable Protocol. `ChangeEvent` round-trips through `model_dump_json`. No filesystem or watchdog import in this ticket.
