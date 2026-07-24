# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.
"""Watchdog-backed :class:`RealFileWatcher` — the production ``FileWatcher`` port.

This is the concrete realization of the deliberate MVP "no watcher" extension
flagged in the :mod:`~factory_console.file_adapter.watcher` port (T39): a single,
opt-in, single-process, read-only component that observes the two subtrees v1
cares about — planning docs under ``docs/planning/**`` and factory lane markers
under ``.factory/run-state/**`` — and streams project-relative
:class:`ChangeEvent`s to per-client subscribers so the backend SSE endpoint can
drive the SPA's live refresh.

Design (all three properties are load-bearing):

- **Read-only** — a watcher only *observes*; it never writes, creates, or deletes
  under the target project (guard-tested, mirroring
  :mod:`~factory_console.file_adapter.run_state`).
- **Project-relative paths only** — every emitted path is relativized against the
  project root, so the host's absolute filesystem layout never reaches the wire
  (also pinned on the :class:`ChangeEvent` schema).
- **Thread → loop safe** — watchdog fires callbacks on its own observer thread;
  this class NEVER touches the asyncio loop or subscriber queues from that thread.
  It hands every observation to the loop via
  :meth:`asyncio.AbstractEventLoop.call_soon_threadsafe`, and all debounce state
  and fan-out then run on the loop thread where they are race-free.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from factory_console.domain.watch import ChangeEvent

# Coalesce window: repeated raw events for the same relative path within this many
# seconds collapse into a single ChangeEvent, so one editor save (which watchdog
# reports as a created + one-or-more modified burst) yields exactly one event.
_DEBOUNCE_SECONDS = 0.15

# The four verbs watchdog emits; also the ChangeEvent.kind literal set. Anything
# outside this set is skipped defensively rather than fed to the model.
_ALLOWED_KINDS = frozenset({"created", "modified", "deleted", "moved"})

# Relative-path prefix that marks the factory run-state subtree; everything else
# under the watched roots is planning scope. Matches the ticket's scope rule and
# the primary run-state location in ``run_state.find_run_state_dir``.
_RUN_STATE_PREFIX = ".factory/run-state"


class _ChangeEventHandler(FileSystemEventHandler):
    """Translate raw watchdog events into project-relative dispatches.

    Runs on the watchdog observer thread. It does only pure mapping work
    (relativize the path, derive the scope, validate the verb) and then hands the
    result to the owning :class:`RealFileWatcher`, which forwards it to the loop.
    """

    def __init__(self, watcher: RealFileWatcher) -> None:
        self._watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        # Ignore directory events: a file create/modify already yields its own
        # event, and folding the parent-directory notification in would double up
        # a single save.
        if event.is_directory:
            return
        kind = event.event_type
        if kind not in _ALLOWED_KINDS:
            return
        # For a ``moved`` event the file now lives at ``dest_path`` — an atomic
        # editor save arrives as a temp-file -> real-name rename, so ``src_path``
        # is the temp origin and only ``dest_path`` names the ticket that
        # actually changed. Prefer the in-root destination; fall back to
        # ``src_path`` when there is no destination or it lands outside the
        # watched tree (a move OUT of the project).
        raw_path = event.src_path
        if kind == "moved":
            dest_path = getattr(event, "dest_path", "") or ""
            if dest_path:
                try:
                    Path(dest_path).relative_to(self._watcher.project_root)
                except ValueError:
                    pass
                else:
                    raw_path = dest_path
        try:
            rel_path = Path(raw_path).relative_to(self._watcher.project_root).as_posix()
        except ValueError:
            # Outside the project root (should not happen for scheduled roots) —
            # skip rather than leak an out-of-tree or absolute path.
            return
        scope = (
            "run-state"
            if rel_path == _RUN_STATE_PREFIX or rel_path.startswith(_RUN_STATE_PREFIX + "/")
            else "planning"
        )
        self._watcher._dispatch_from_thread(kind, scope, rel_path)


class RealFileWatcher:
    """Production ``FileWatcher``: a watchdog ``Observer`` over the two v1 subtrees.

    Opt-in, single-process, and read-only — constructed and :meth:`start`-ed only
    when the backend enables live updates. Satisfies the ``FileWatcher`` Protocol
    structurally (``start`` / ``stop`` / ``subscribe``) so the backend swaps it in
    for :class:`~factory_console.file_adapter.watcher.FakeFileWatcher`
    transparently. See the module docstring for the read-only, project-relative,
    and thread→loop-safety invariants.
    """

    def __init__(self, project_root: Path) -> None:
        # Resolve once so relativization is stable and symlinked roots (e.g. the
        # macOS ``/tmp`` → ``/private/tmp`` link) match the paths watchdog reports.
        self.project_root = Path(project_root).resolve()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._observer: Observer | None = None
        # Subscribers is a set (not a list) so unregistration uses ``discard`` —
        # a bare ``list.remove`` would trip the read-only AST guard on the name.
        self._subscribers: set[asyncio.Queue[ChangeEvent]] = set()
        # Debounce state — only ever touched on the loop thread.
        self._pending: dict[str, tuple[str, str]] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}

    # -- lifecycle ---------------------------------------------------------- #

    def start(self) -> None:
        """Begin observing the watched roots (idempotent — a second call is a no-op).

        Called from the backend's async lifespan, so it captures the running loop
        here (there is no running loop on the watchdog thread later). Schedules a
        recursive handler on each of ``docs/planning`` and ``.factory/run-state``
        that exists on disk; missing roots are skipped.
        """
        if self._observer is not None:
            return
        self._loop = asyncio.get_running_loop()
        observer = Observer()
        handler = _ChangeEventHandler(self)
        for root in (
            self.project_root / "docs" / "planning",
            self.project_root / ".factory" / "run-state",
        ):
            if root.is_dir():
                observer.schedule(handler, str(root), recursive=True)
        # Even with no root present the observer is started so ``stop`` stays
        # symmetric; it simply has nothing scheduled.
        self._observer = observer
        observer.start()

    def stop(self) -> None:
        """Halt observing and join the observer thread (idempotent, never raises).

        Safe if never started or already stopped. Cancels any pending debounce
        timers so no coalesced event fires after shutdown, then joins so no
        watchdog thread lingers.
        """
        observer = self._observer
        if observer is not None:
            observer.stop()
            observer.join()
            self._observer = None
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
        self._pending.clear()

    # -- fan-out ------------------------------------------------------------ #

    async def subscribe(self) -> AsyncIterator[ChangeEvent]:
        """Register a fresh per-client queue and yield each awaited event.

        Identical external contract to
        :meth:`~factory_console.file_adapter.watcher.FakeFileWatcher.subscribe`:
        the queue registers on first await and is unregistered in a ``finally`` so
        a cancelled or disconnected client leaks nothing and never blocks others.
        """
        queue: asyncio.Queue[ChangeEvent] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    # -- thread → loop bridge + debounce ------------------------------------ #

    def _dispatch_from_thread(self, kind: str, scope: str, rel_path: str) -> None:
        """Hand a mapped observation to the loop (called on the watchdog thread).

        The ONLY cross-thread hop: it never touches the loop or the queues
        directly. Everything downstream runs on the loop thread.
        """
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._coalesce, kind, scope, rel_path)

    def _coalesce(self, kind: str, scope: str, rel_path: str) -> None:
        """Debounce per relative path (loop thread): (re)arm the flush timer."""
        existing = self._timers.get(rel_path)
        if existing is not None:
            existing.cancel()
        self._pending[rel_path] = (kind, scope)
        assert self._loop is not None  # set before any event can arrive
        self._timers[rel_path] = self._loop.call_later(_DEBOUNCE_SECONDS, self._flush, rel_path)

    def _flush(self, rel_path: str) -> None:
        """Emit the coalesced event for ``rel_path`` to all subscribers (loop thread)."""
        self._timers.pop(rel_path, None)
        pending = self._pending.pop(rel_path, None)
        if pending is None:
            return
        kind, scope = pending
        event = ChangeEvent(
            kind=kind,  # type: ignore[arg-type]  # validated against _ALLOWED_KINDS
            path=rel_path,
            scope=scope,  # type: ignore[arg-type]  # derived planning | run-state
            at=datetime.now(UTC),
        )
        # Snapshot the subscribers so a subscriber unregistering mid-iteration
        # (client disconnect) cannot mutate the set under us.
        for queue in list(self._subscribers):
            queue.put_nowait(event)
