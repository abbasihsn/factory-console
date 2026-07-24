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
from typing import get_args

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from factory_console.domain.watch import ChangeEvent
from factory_console.file_adapter.run_state import RUN_STATE_RELATIVE_LOCATIONS

# Coalesce window: repeated raw events for the same relative path within this many
# seconds collapse into a single ChangeEvent, so one editor save (which watchdog
# reports as a created + one-or-more modified burst) yields exactly one event.
_DEBOUNCE_SECONDS = 0.15

# The change verbs we surface, derived from the single source of truth —
# ``ChangeEvent.kind``'s Literal — so the guard here and the model can never
# drift (add a verb to the Literal and it is honored here automatically). Any
# raw watchdog event type outside this set is skipped defensively.
_ALLOWED_KINDS = frozenset(get_args(ChangeEvent.model_fields["kind"].annotation))

# Relative-path prefixes that mark the factory run-state subtree; everything else
# under the watched roots is planning scope. Derived from the SAME source of
# truth the prober uses (``run_state.RUN_STATE_RELATIVE_LOCATIONS``) so the two
# modules cannot drift. BOTH documented locations are recognized: the primary
# ``.factory/run-state`` and the ``docs/planning/.run-state`` fallback — the
# latter IS observed (``docs/planning`` is scheduled ``recursive=True``), so
# without it a run-state marker in the fallback layout would be mis-tagged
# ``planning`` and refresh the wrong pane.
_RUN_STATE_PREFIXES = tuple(loc.as_posix() for loc in RUN_STATE_RELATIVE_LOCATIONS)


class _ChangeEventHandler(FileSystemEventHandler):
    """Translate raw watchdog events into project-relative dispatches.

    Runs on the watchdog observer thread. It does only pure mapping work
    (relativize the path, derive the scope, validate the verb) and then hands the
    result to the owning :class:`RealFileWatcher`, which forwards it to the loop.
    """

    def __init__(self, watcher: RealFileWatcher) -> None:
        self._watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
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
                    Path(dest_path).relative_to(self._watcher._project_root)
                except ValueError:
                    pass
                else:
                    raw_path = dest_path
        try:
            rel_path = Path(raw_path).relative_to(self._watcher._project_root).as_posix()
        except ValueError:
            # Outside the project root (should not happen for scheduled roots) —
            # skip rather than leak an out-of-tree or absolute path.
            return
        matched_prefix = next(
            (p for p in _RUN_STATE_PREFIXES if rel_path == p or rel_path.startswith(p + "/")),
            None,
        )
        scope = "run-state" if matched_prefix is not None else "planning"
        if event.is_directory and not self._is_run_state_marker_dir(kind, rel_path, matched_prefix):
            # Directory events carry signal ONLY as a run-state marker directory.
            # A run-state marker CAN itself be a directory
            # (``run_state.probe_ticket_state`` resolves a ``<state>/<ticket_id>``
            # marker as a file OR a directory), so its create/delete/move is a
            # real transition. Every other directory event is noise: the
            # parent-folder echo of a planning file save, a ``modified``
            # notification, and the watched-root / state-dir events macOS FSEvents
            # replays. Dropping them keeps one save (or one transition) to one
            # event.
            return
        self._watcher._dispatch_from_thread(kind, scope, rel_path)

    @staticmethod
    def _is_run_state_marker_dir(kind: str, rel_path: str, matched_prefix: str | None) -> bool:
        """True if a directory event is a run-state ``<state>/<ticket_id>`` marker.

        A marker lives exactly two segments below a run-state root, so its path
        has a ``<state>/<ticket_id>`` remainder (contains a ``/``). This excludes
        the run-state root itself and a bare ``<state>`` directory — the levels
        macOS FSEvents emits spurious create/modify events on — and the
        ``modified`` verb, which is only ever a parent-folder echo.
        """
        if matched_prefix is None or kind == "modified":
            return False
        if not rel_path.startswith(matched_prefix + "/"):
            return False
        remainder = rel_path[len(matched_prefix) + 1 :]
        return "/" in remainder


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
        # macOS ``/tmp`` → ``/private/tmp`` link) match the paths watchdog
        # reports. Non-public (only the same-module handler reads it) so the
        # instance's public surface stays exactly the FileWatcher port, like
        # FakeFileWatcher.
        self._project_root = Path(project_root).resolve()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._observer: Observer | None = None
        # Subscribers is a set (not a list) so unregistration uses ``discard`` —
        # a bare ``list.remove`` would trip the read-only AST guard on the name.
        self._subscribers: set[asyncio.Queue[ChangeEvent]] = set()
        # Debounce state — only ever touched on the loop thread.
        self._pending: dict[str, tuple[str, str]] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        # Latched on stop(): a dispatch the watchdog thread queued via
        # call_soon_threadsafe just before the observer stopped can still be
        # sitting in the loop's callback queue and run after stop() has cleared
        # the debounce state. Both the cross-thread hand-off and the loop-thread
        # coalesce check this so no ChangeEvent fires after shutdown.
        self._stopped = False

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
        self._stopped = False
        self._loop = asyncio.get_running_loop()
        observer = Observer()
        handler = _ChangeEventHandler(self)
        # The planning root is its own concept (planning scope). Each run-state
        # location comes from the SHARED constant — not a re-typed literal — so
        # the scheduled roots cannot drift from the scope tags derived from the
        # same tuple. A run-state location already under the recursive planning
        # watch (the ``docs/planning/.run-state`` fallback) is skipped to avoid
        # scheduling it twice.
        planning_root = self._project_root / "docs" / "planning"
        roots = [planning_root]
        for relative in RUN_STATE_RELATIVE_LOCATIONS:
            candidate = self._project_root / relative
            try:
                candidate.relative_to(planning_root)
            except ValueError:
                roots.append(candidate)
        for root in roots:
            if root.is_dir():
                observer.schedule(handler, str(root), recursive=True)
        # Even with no root present the observer is started so ``stop`` stays
        # symmetric; it simply has nothing scheduled.
        self._observer = observer
        observer.start()

    def stop(self) -> None:
        """Halt observing and join the observer thread (idempotent, never raises).

        Safe if never started or already stopped. Latches the ``_stopped`` guard
        first so any dispatch already queued on the loop no-ops, cancels any
        pending debounce timers so no coalesced event fires after shutdown, then
        joins so no watchdog thread lingers.
        """
        self._stopped = True
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
        if loop is None or self._stopped:
            return
        loop.call_soon_threadsafe(self._coalesce, kind, scope, rel_path)

    def _coalesce(self, kind: str, scope: str, rel_path: str) -> None:
        """Debounce per relative path (loop thread): (re)arm the flush timer."""
        # A dispatch queued just before stop() can still be delivered here after
        # stop() cleared the debounce state; drop it so no timer re-arms.
        if self._stopped:
            return
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
