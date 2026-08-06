# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.
"""Watchdog-backed :class:`RealFileWatcher` — the production ``FileWatcher`` port.

This is the concrete realization of the deliberate MVP "no watcher" extension
flagged in the :mod:`~factory_console.file_adapter.watcher` port (T39): a single,
opt-in, single-process, read-only component that observes what the console cares
about — planning docs under ``docs/planning/**``, factory lane markers under
``.factory/run-state/**``, and a small SET of factory JSON artefacts (today
``.factory/run-state.json``, the spend ledger ``.factory/metrics/ledger.jsonl``, and
the ``/runs`` sources: the ``.factory/results/`` and ``.factory/receipts/``
directories plus ``.factory/last-stop.json``) — and streams project-relative
:class:`ChangeEvent`s to per-client subscribers so the backend SSE endpoint can drive
the SPA's live refresh.

That set is NOT enumerated here. It is
:data:`~factory_console.domain.watched_artifacts.WATCHED_JSON_ARTIFACTS`, the one
list the readers take their path constants from as well, so an artefact the console
learns to read cannot be one the watcher never learns to schedule — the omission
that left run-state unwatched (T91), then the ledger (T95), then results, receipts
and last stop (T99).

Both of that list's shapes are reached through a NON-RECURSIVE directory watch, and
which directory is what the entry's
:data:`~factory_console.domain.watched_artifacts.ArtifactKind` decides. A ``"file"``
artefact is watched by scheduling its PARENT and filtering events down to that one
filename; a ``"dir"`` artefact (results, receipts — a lane's file lands at a
``<ticket_id>.json`` no constant can spell in advance) by scheduling that directory
ITSELF and taking any file directly inside it. Neither is watched as a file: watchdog
schedules directories, and the factory replaces these via ``mktemp`` + ``mv``
(INV-03), so anything bound to an inode would go quiet after the first update. Each
such watch is a means to observe exactly its artefact — everything else under it is
dropped in the handler, so scope does not widen to the rest of ``.factory``.

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
from factory_console.domain.watched_artifacts import WATCHED_JSON_ARTIFACTS
from factory_console.file_adapter.run_state import (
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

# The factory FILE artefacts, as project-relative POSIX path -> ``ChangeScope`` —
# the exact set an event under a JSON parent watch must match to be surfaced, and
# what each match MEANS. Derived from
# :data:`~factory_console.domain.watched_artifacts.WATCHED_JSON_ARTIFACTS`, the same
# single list the readers use and the same list ``start()`` schedules from, so a
# path can never be scheduled without a scope or scoped without being scheduled.
#
# The scope is looked up rather than hardcoded (T95): before the ledger joined, every
# file match was ``run-state`` and the constant was a bare frozenset. A run-state file
# event is still scoped exactly like a directory-source one — the scope says WHAT
# changed, not which on-disk form stored it, and a subscriber must not be able to tell
# the two apart.
_WATCHED_JSON_SCOPES: dict[str, ChangeScope] = {
    relative.as_posix(): scope for scope, relative, kind in WATCHED_JSON_ARTIFACTS if kind == "file"
}

# The factory ARTEFACT DIRECTORIES, as project-relative POSIX directory ->
# ``ChangeScope``: any FILE whose immediate parent is one of these is that artefact
# (T99). Derived from the same single list, and disjoint from the map above by
# construction — an entry declares one ``ArtifactKind``, so a path is matched either
# by its own name or by its parent's, never by both.
#
# A directory match is the only way to express ``.factory/results/<ticket_id>.json``,
# where the FILENAME is a lane's ticket id and no constant can hold it. Matching on
# the parent instead is exactly as narrow: the watch is non-recursive, so the only
# events that reach it are the directory's own direct children.
_WATCHED_JSON_DIR_SCOPES: dict[str, ChangeScope] = {
    relative.as_posix(): scope for scope, relative, kind in WATCHED_JSON_ARTIFACTS if kind == "dir"
}


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
        file_scope = _WATCHED_JSON_SCOPES.get(rel_path)
        if file_scope is not None and not event.is_directory:
            # A watched FILE artefact (T91 for run-state, T95 for the ledger, T99
            # for last stop), dispatched under the scope the shared list pairs it
            # with. Checked BEFORE the prefix rule below, which cannot match any of
            # them: ``.factory/run-state.json`` is a sibling of
            # ``.factory/run-state``, not a path under it, and the others are under
            # neither.
            self._watcher._dispatch_from_thread(kind, file_scope, rel_path)
            return
        parent = Path(rel_path).parent.as_posix()
        dir_scope = _WATCHED_JSON_DIR_SCOPES.get(parent)
        if dir_scope is not None and not event.is_directory and rel_path.endswith(".json"):
            # A file directly inside a watched artefact DIRECTORY — one lane's
            # result or receipt, whose filename is a ticket id (T99). The scope
            # comes from the directory the shared list declared, so a new lane's
            # artefact is matched the moment it appears, under no fixed name.
            # Ordered after the exact-path check so a file artefact declared inside
            # a watched directory would keep its OWN scope; the two maps are
            # disjoint today, so this order is a statement of precedence rather
            # than a live case. Filtered to ``.json`` names because the factory
            # writes these via mktemp + mv IN THE SAME DIRECTORY (INV-03): an
            # unfiltered match would dispatch the temp file's own create as a
            # second ``runs`` event for every write, on a filename that is not
            # the artefact and should never reach a subscriber.
            self._watcher._dispatch_from_thread(kind, dir_scope, rel_path)
            return
        if (
            event.is_directory
            and kind == "created"
            and rel_path in _WATCHED_JSON_DIR_SCOPES
        ):
            # The watched directory ITSELF just came into existence — results or
            # receipts created after start() (both are absent on a fresh clone,
            # since ``.factory/`` is gitignored, so this is the common case, not
            # an edge one). ``start()`` only schedules a "dir" target that already
            # existed on disk; without this, the directory's later creation is a
            # directory event whose parent is a json-only root and is silently
            # dropped by the branch below, and nothing ever schedules it — the
            # artefact stays unwatched for the rest of the process's life, one
            # directory deeper than the failure T99 fixed. Scheduling here, on the
            # live observer, is what lets the files that land inside it afterwards
            # reach the ``dir_scope`` branch above at all.
            self._watcher._schedule_late(rel_path, self)
            return
        if parent in self._watcher._json_only_roots:
            # A NAMED drop, not a silent one (T99, criterion 2). This root is
            # scheduled ONLY to observe the artefact(s) the two maps above declare,
            # and this event matched neither, so it is one of exactly two things:
            # an unrelated neighbour under a watched parent (another file in
            # ``.factory``), or a DIRECTORY event where only files carry artefact
            # signal — a subdirectory appearing under ``.factory/results``, or the
            # watched directory's own create/delete reaching its parent's watch.
            # Both are noise for the artefact this root exists to see, and dropping
            # them is what keeps the watch from widening to the rest of
            # ``.factory`` — falling through would tag them ``planning`` and
            # refresh a pane nothing changed in.
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
    """Production ``FileWatcher``: a watchdog ``Observer`` over what the console observes.

    That is the two planning/run-state SUBTREES plus the JSON artefacts of
    :data:`~factory_console.domain.watched_artifacts.WATCHED_JSON_ARTIFACTS` — see
    the module docstring for which non-recursive directory each of that list's two
    kinds is reached through. The set is deliberately not restated here or there: a
    list kept in two places is what let the JSON run-state source go unwatched (T91),
    then the spend ledger (T95), then the ``/runs`` artefacts (T99).

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
        # Project-relative POSIX directories scheduled ONLY to observe watched JSON
        # artefacts — a parent holding a named file (``.factory`` for
        # ``run-state.json`` and ``last-stop.json``, ``.factory/metrics`` for the
        # spend ledger), or an artefact directory watched for its own contents
        # (``.factory/results``, ``.factory/receipts``). Filled by start(); the
        # handler drops every event under one of these that is not a watched artefact
        # itself, so such a watch never widens scope. A directory ALREADY covered by a
        # substantive watch is absent here — its other entries keep whatever meaning
        # that watch gives them.
        self._json_only_roots: frozenset[str] = frozenset()
        # "dir"-kind artefact directories (results, receipts) scheduled AFTER
        # start(), because they did not exist on disk yet when it ran — see
        # ``_schedule_late``. Guards against scheduling the same directory twice
        # on the observer if its creation is reported more than once.
        self._late_watched: set[str] = set()
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
        that exists on disk, plus a NON-recursive one per watched JSON artefact —
        on the PARENT of a ``"file"`` artefact (``.factory`` for ``run-state.json``
        and ``last-stop.json``, ``.factory/metrics`` for the ledger) and on a
        ``"dir"`` artefact ITSELF (``.factory/results``, ``.factory/receipts``) —
        whose events the handler filters down to that artefact. Missing roots are
        skipped.
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
        # Recursive roots first, then the JSON artefacts' directories — so an already
        # listed root WINS. Scheduling one physical path twice would report every
        # event under it twice, and a directory already covered by a recursive watch
        # needs no watch of its own; being watched for more than its artefact, it
        # is also not a json-only root, so the handler leaves its other entries
        # alone. Only these are scheduled non-recursively: they exist to see ONE
        # artefact, and recursing would drag all of ``.factory`` in for no gain —
        # including, for the results and receipts directories, whatever a lane may
        # one day nest inside them, which is not the per-ticket file ``/runs`` reads.
        #
        # "Already covered" therefore has to consult the RECURSION FLAG, not just
        # containment: only a RECURSIVE root observes its descendants. A
        # non-recursive root covers the path itself and nothing below it, so it
        # discharges a later JSON directory only when it IS that directory — hence
        # the explicit ``root == target``, which containment alone would also satisfy
        # (``Path.is_relative_to`` is true of a path against itself) but which must
        # not be reached THROUGH the descendant branch. Treating a non-recursive
        # root as covering its descendants would skip a nested JSON directory as
        # "already watched" by a watch that cannot see into it, and skip it out of
        # ``json_only_roots`` too — an artefact scheduled nowhere and filtered
        # nowhere, which is silently no live updates: precisely the failure T91
        # exists to fix, reintroduced one directory deeper. That case is no longer
        # hypothetical since T95: the ledger's parent ``.factory/metrics`` sits
        # exactly one level under ``.factory``, the non-recursive parent of
        # ``run-state.json``, so this is the branch that keeps the ledger watched —
        # and since T99 it carries ``.factory/results`` and ``.factory/receipts``,
        # which sit at that same depth, twice more.
        #
        # The loop iterates the SHARED artefact list rather than a run-state-only
        # one; only each entry's PATH and KIND matter here, since scheduling a
        # directory says nothing about what a change there means — the handler's
        # scope maps own that.
        scheduled: list[tuple[Path, bool]] = [(root, True) for root in roots]
        json_only_roots: set[str] = set()
        for _scope, relative, kind in WATCHED_JSON_ARTIFACTS:
            # WHICH directory to watch is the entry's kind, and nothing else: a
            # ``"file"`` artefact is seen from its PARENT, a ``"dir"`` artefact
            # (results, receipts) from ITSELF — its files are named after ticket
            # ids, so its parent would be one level too high to filter by name and
            # the directory is what the handler matches on instead. Everything
            # after this line is identical for the two, which is the point of
            # putting the discriminator on the shared list rather than branching
            # into a second scheduling path.
            watched_dir = relative.parent if kind == "file" else relative
            target = self._project_root / watched_dir
            if any(
                root == target or (recursive and target.is_relative_to(root))
                for root, recursive in scheduled
            ):
                continue
            scheduled.append((target, False))
            json_only_roots.add(watched_dir.as_posix())
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

    # -- late scheduling ------------------------------------------------------ #

    def _schedule_late(self, rel_path: str, handler: _ChangeEventHandler) -> None:
        """Schedule a "dir"-kind artefact directory that did not exist at ``start()``.

        Called from :meth:`_ChangeEventHandler.on_any_event` on the WATCHDOG
        THREAD, not the loop — same as :meth:`_dispatch_from_thread`, but this one
        talks to the observer instead of the loop, and ``Observer.schedule`` is
        itself thread-safe (watchdog's own documented use for watching a
        directory the moment it appears), so no ``call_soon_threadsafe`` hop is
        needed here the way it is for debounce state and the subscriber hub.
        """
        if self._observer is None or rel_path in self._late_watched:
            return
        self._late_watched.add(rel_path)
        self._observer.schedule(handler, str(self._project_root / rel_path), recursive=False)

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
