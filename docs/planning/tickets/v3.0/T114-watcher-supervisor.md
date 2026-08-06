# [T114] Watcher supervisor — one watcher for the selected project, swapped on selection change

milestone: v3.0 · track: backend · depends_on: T111, T39, T40, T44 · provides: `services/watcher_supervisor.py` owning AT MOST ONE live `FileWatcher` — the selected project's — started by the lifespan, swapped on selection change, exposed through `get_file_watcher` with a monotonic generation counter.

## Context

`RealFileWatcher` is constructed with ONE root at boot, resolves that root in `__init__`, schedules
watches in `start()`, and is driven by `_watcher_lifespan`. With a switchable project that has to
change, and there are three options:

- **One watcher per registered project — rejected.** Each watcher is a watchdog `Observer` thread
  with recursive watches over `docs/planning/**` plus five `.factory` targets; N of them burn N
  threads and N× the OS watch descriptors (a real limit on Linux inotify) to feed a view that
  displays exactly one project at a time. The all-projects dashboard that would justify it is v3.2.
- **Re-root the existing instance in place — rejected.** It means mutating `_project_root`,
  unscheduling and rescheduling every watch, and reconciling in-flight debounce timers and
  `_late_watched` state against a root they no longer belong to — a new state machine inside a file
  the backend track does not own.
- **Chosen: a supervisor that owns at most one watcher and REPLACES it.** Stop the old one (which
  already joins its observer thread, cancels its debounce timers and latches `_stopped`), construct a
  fresh watcher on the new root, start it. The swap reuses `RealFileWatcher`'s existing, tested
  lifecycle verbatim and adds no state to it. Cost: subscribers of the old watcher stop receiving
  events — which T115 turns into a correct, self-healing SSE contract rather than a silent stall.

The supervisor builds watchers through an injected `watcher_factory`, so the backend still never
imports `watcher_real.py`; `cli.py` and `create_dev_app` pass the concrete, exactly as the ownership
table permits.

## Staged approach

1. CREATE `server/factory_console/services/watcher_supervisor.py`:
   - `class WatcherSupervisor` with `__init__(self, watcher_factory: Callable[[Path], FileWatcher] |
     None, *, initial: FileWatcher | None = None)`. `initial` preserves today's
     `create_app(file_watcher=...)` path so every existing test and the pinned CLI boot keep their
     exact behaviour.
   - State: `_current: FileWatcher | None`, `_root: Path | None`, `_generation: int` (starts at 0,
     incremented on every swap), `_started: bool`.
   - `start(root: Path | None) -> None` — called from the lifespan, from inside the async context, so
     a `RealFileWatcher` captures the running loop exactly as it does today.
   - `retarget(root: Path | None) -> None` — the on-change hook. No-op when `root == self._root`.
     Otherwise `stop()` the old, bump `_generation`, build + `start()` the new (skip when `root is
     None` or no factory is available). Every step wrapped so a watcher that fails to start leaves
     the supervisor watcher-less rather than half-swapped — live updates are opt-in and must never
     take the request path down.
   - `stop() -> None` — idempotent, always joins; called from the lifespan `finally`.
   - `current() -> FileWatcher | None` and `generation() -> int`.
   - **`retarget` runs on the loop thread from a handler (`PUT /projects/current`) and calls
     `Observer.stop()/join()`, which BLOCKS on a thread join.** Do the swap inside
     `anyio.to_thread.run_sync` at the caller (the selection hook adapter in `app.py`) rather than
     inline — a join on the event loop is exactly the house rule's failure mode. Document this at
     both ends.
2. EDIT `server/factory_console/app.py`:
   - Build `app.state.watcher_supervisor = WatcherSupervisor(watcher_factory, initial=file_watcher)`;
     add `watcher_factory: Callable[[Path], FileWatcher] | None = None` to `create_app`'s keyword
     params.
   - Subscribe the supervisor to `app.state.selection` (T111's `on_change` hook) so a selection
     change retargets it.
   - Drive the supervisor from `_watcher_lifespan` (`start(resolved_initial_root)` / `stop()` in
     `finally`); keep the function name and its docstring shape so the diff stays readable and
     `tests/integration/test_app_lifespan.py` keeps its subject.
3. EDIT `server/factory_console/api/deps.py`: `get_file_watcher` returns `supervisor.current()` when
   a supervisor is bound, else falls back to `app.state.file_watcher`. Still returns `None` (never
   raises) — the opt-in contract is unchanged. Add `get_watcher_supervisor(request)`.
4. CREATE `tests/unit/test_watcher_supervisor.py` — swap semantics with a fake factory: generation
   increments; the old watcher is stopped exactly once; a same-root retarget is a no-op; a `None`
   root releases; a factory that raises leaves `current() is None`; `stop()` is idempotent.
5. EDIT `tests/integration/test_app_lifespan.py` — the supervisor starts/stops the initial watcher;
   `create_app(file_watcher=...)` with no factory still behaves exactly as today.

## Critical files

- `server/factory_console/services/watcher_supervisor.py` (create)
- `server/factory_console/app.py` (modify — aggregation file)
- `server/factory_console/api/deps.py` (modify — aggregation file)
- `tests/unit/test_watcher_supervisor.py` (create)
- `tests/integration/test_app_lifespan.py` (modify)

## Interface & data

Ports by reference, not redefined: `FileWatcher` Protocol (`file_adapter/watcher.py`, T39) —
`start()` / `stop()` / `subscribe()`. The supervisor consumes it and adds nothing to it.

New surface: `WatcherSupervisor.start(root)`, `.retarget(root)`, `.stop()`,
`.current() -> FileWatcher | None`, `.generation() -> int`;
`create_app(..., watcher_factory: Callable[[Path], FileWatcher] | None = None)`;
`app.state.watcher_supervisor`.

Wire contract: none — nothing new is serialised, so the disclosure rule is not engaged.
DB ops: none. NFR flags: the swap performs a thread join, so it is offloaded with
`anyio.to_thread.run_sync` at the calling handler, never run inline on the loop; the supervisor is
single-loop and holds no lock (single worker, ARCHITECTURE.md → Concurrency); a swap failure degrades
to no-watcher and never fails a request.

## Verification

`python -m pytest tests/unit/test_watcher_supervisor.py tests/integration/test_app_lifespan.py
tests/integration/test_real_file_watcher.py -q`, then `python -m pytest -q`. `make lint`.
Manual: boot on a fixture, touch a planning file, confirm `/api/v1/events` still emits a change frame
(pinned mode must be unchanged).
