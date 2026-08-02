# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.
"""Watchdog-backed :class:`RealFileWatcher` — the production ``FileWatcher`` port.

This is the concrete realization of the deliberate MVP "no watcher" extension
flagged in the :mod:`~factory_console.file_adapter.watcher` port (T39): a single,
opt-in, single-process, read-only component that observes what v1 cares about —
planning docs under ``docs/planning/**``, factory lane markers under
``.factory/run-state/**``, and the factory's primary run-state artifact, the FILE
``.factory/run-state.json`` — and streams project-relative :class:`ChangeEvent`s
to per-client subscribers so the backend SSE endpoint can drive the SPA's live
refresh.

The file source is watched by scheduling its PARENT directory non-recursively and
filtering events down to that one filename (T91). Two reasons it cannot be watched
directly: watchdog schedules directories, and the factory replaces the file via
``mktemp`` + ``mv`` (INV-03), so anything bound to the file's inode would go quiet
after the first update. That parent watch is a means to observe ONE file — every
other entry under it is dropped in the handler, so scope does not widen to the
rest of ``.factory``.

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
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast, get_args

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from factory_console.domain.watch import ChangeEvent, ChangeKind, ChangeScope
from factory_console.file_adapter.run_state import (
    RUN_STATE_JSON_RELATIVE_LOCATIONS,
    RUN_STATE_RELATIVE_LOCATIONS,
    is_run_state_marker,
)
from factory_console.file_adapter.watcher import _SubscriberHub

# Coalesce window: repeated raw events for the same relative path within this many
# seconds collapse into a single ChangeEvent, so one editor save (which watchdog
# reports as a created + one-or-more modified burst) yields exactly one event.
_DEBOUNCE_SECONDS = 0.15

# The change verbs we surface, derived from the shared ``ChangeKind`` type so the
# guard here and the ChangeEvent model share one source of truth (add a verb to
# ``ChangeKind`` and it is honored here automatically). Any raw watchdog event
# type outside this set is skipped defensively.
_ALLOWED_KINDS = frozenset(get_args(ChangeKind))

# Relative-path prefixes that mark the factory run-state subtree; everything else
# under the watched roots is planning scope. Derived from the SAME source of
# truth the prober uses (``run_state.RUN_STATE_RELATIVE_LOCATIONS``) so the two
# modules cannot drift. BOTH documented locations are recognized: the primary
# ``.factory/run-state`` and the ``docs/planning/.run-state`` fallback — the
# latter IS observed (``docs/planning`` is scheduled ``recursive=True``), so
# without it a run-state marker in the fallback layout would be mis-tagged
# ``planning`` and refresh the wrong pane.
_RUN_STATE_PREFIXES = tuple(loc.as_posix() for loc in RUN_STATE_RELATIVE_LOCATIONS)

# The run-state FILE sources, as project-relative POSIX paths — the exact set an
# event under a JSON parent watch must match to be surfaced. Same single source
# of truth as the prefixes above, its ``json`` half. An event matching one of
# these is scoped ``run-state`` like any directory-source event: the scope says
# WHAT changed, not which on-disk form stored it, and a subscriber must not be
# able to tell the two apart.
_RUN_STATE_JSON_PATHS = frozenset(loc.as_posix() for loc in RUN_STATE_JSON_RELATIVE_LOCATIONS)


class _ChangeEventHandler(FileSystemEventHandler):
    """Translate raw watchdog events into project-relative dispatches.

    Runs on the watchdog observer thread. It does only pure mapping work
    (relativize the path, derive the scope, validate the verb) and then hands the
    result to the owning :class:`RealFileWatcher`, which forwards it to the loop.
    """

    def __init__(self, watcher: RealFileWatcher) -> None:
        self._watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        raw_kind = event.event_type
        if raw_kind not in _ALLOWED_KINDS:
            return
        kind = cast(ChangeKind, raw_kind)  # narrowed once, at the raw-event boundary
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
        if rel_path in _RUN_STATE_JSON_PATHS and not event.is_directory:
            # The run-state FILE source (T91). Scoped exactly like a
            # directory-source event, and checked BEFORE the prefix rule below,
            # which cannot match it: ``.factory/run-state.json`` is a sibling of
            # ``.factory/run-state``, not a path under it.
            self._watcher._dispatch_from_thread(kind, "run-state", rel_path)
            return
        if Path(rel_path).parent.as_posix() in self._watcher._json_only_roots:
            # This root is scheduled ONLY to see the run-state file above, and
            # this event is not it. Dropping everything else here is what keeps
            # the watch from widening to the rest of ``.factory``.
            return
        scope: ChangeScope = (
            "run-state"
            if any(rel_path == p or rel_path.startswith(p + "/") for p in _RUN_STATE_PREFIXES)
            else "planning"
        )
        if event.is_directory and (kind == "modified" or not is_run_state_marker(rel_path)):
            # Directory events carry signal ONLY as a run-state marker directory.
            # A run-state marker CAN itself be a directory
            # (``run_state.probe_ticket_state`` resolves a ``<state>/<ticket_id>``
            # marker as a file OR a directory), so its create/delete/move is a
            # real transition — ``run_state.is_run_state_marker`` owns that layout
            # rule. Every other directory event is noise this watcher drops: the
            # parent-folder echo of a planning file save, a ``modified``
            # notification, and the watched-root / bare-state-dir events macOS
            # FSEvents replays. Dropping them keeps one save (or one transition)
            # to one event.
            return
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
        # macOS ``/tmp`` → ``/private/tmp`` link) match the paths watchdog
        # reports. Non-public (only the same-module handler reads it) so the
        # instance's public surface stays exactly the FileWatcher port, like
        # FakeFileWatcher.
        self._project_root = Path(project_root).resolve()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._observer: Observer | None = None
        # Project-relative POSIX directories scheduled ONLY to observe a run-state
        # JSON file inside them (``.factory`` for ``.factory/run-state.json``).
        # Filled by start(); the handler drops every event under one of these that
        # is not the JSON file itself, so the parent watch never widens scope. A
        # JSON parent that is ALREADY covered by a substantive watch is absent
        # here — its other entries keep whatever meaning that watch gives them.
        self._json_only_roots: frozenset[str] = frozenset()
        # The register/unregister/fan-out mechanics are shared with FakeFileWatcher
        # via _SubscriberHub (in watcher.py, NOT this guard-scanned source, so it
        # may use ``list.remove`` freely) — one implementation, no drift.
        self._hub = _SubscriberHub()
        # Debounce state — only ever touched on the loop thread.
        self._pending: dict[str, tuple[ChangeKind, ChangeScope]] = {}
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
        that exists on disk, plus a NON-recursive one on the parent of each
        run-state JSON file (``.factory``) whose events the handler filters down
        to that file. Missing roots are skipped.
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
        # Recursive roots first, then the JSON sources' parents — so an already
        # listed root WINS. Scheduling one physical path twice would report every
        # event under it twice, and a parent already covered by a recursive watch
        # needs no watch of its own; being watched for more than the JSON file, it
        # is also not a json-only root, so the handler leaves its other entries
        # alone. Only these parents are scheduled non-recursively: they exist to
        # see ONE file, and recursing would drag all of ``.factory`` in for no gain.
        scheduled: list[tuple[Path, bool]] = [(root, True) for root in roots]
        json_only_roots: set[str] = set()
        for relative in RUN_STATE_JSON_RELATIVE_LOCATIONS:
            parent = self._project_root / relative.parent
            if any(parent.is_relative_to(root) for root, _ in scheduled):
                continue
            scheduled.append((parent, False))
            json_only_roots.add(relative.parent.as_posix())
        self._json_only_roots = frozenset(json_only_roots)
        for root, recursive in scheduled:
            if root.is_dir():
                observer.schedule(handler, str(root), recursive=recursive)
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

    def subscribe(self) -> AsyncGenerator[ChangeEvent, None]:
        """Register a fresh per-client queue and yield each awaited event.

        Identical external contract to
        :meth:`~factory_console.file_adapter.watcher.FakeFileWatcher.subscribe`
        because both return the same :class:`_SubscriberHub`'s async generator: the
        queue registers on first await and is unregistered in a ``finally`` (which
        runs on close) so a cancelled or disconnected client leaks nothing and
        never blocks others.
        """
        return self._hub.subscribe()

    # -- thread → loop bridge + debounce ------------------------------------ #

    def _dispatch_from_thread(self, kind: ChangeKind, scope: ChangeScope, rel_path: str) -> None:
        """Hand a mapped observation to the loop (called on the watchdog thread).

        The ONLY cross-thread hop: it never touches the loop or the queues
        directly. Everything downstream runs on the loop thread.
        """
        loop = self._loop
        if loop is None or self._stopped:
            return
        loop.call_soon_threadsafe(self._coalesce, kind, scope, rel_path)

    def _coalesce(self, kind: ChangeKind, scope: ChangeScope, rel_path: str) -> None:
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
        event = ChangeEvent(kind=kind, path=rel_path, scope=scope, at=datetime.now(UTC))
        self._hub.fan_out(event)
