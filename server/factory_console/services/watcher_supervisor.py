"""One live :class:`FileWatcher` at a time — the selected project's.

A watcher is constructed around ONE root: ``RealFileWatcher`` resolves it in
``__init__`` and schedules its watches in ``start()``. v3.0 lets the operator switch
projects mid-session, so something has to answer what happens to that watcher. This
module is that answer, and the shape it takes was chosen over two others:

- **One watcher per registered project — rejected.** Each is a watchdog ``Observer``
  thread with recursive watches over ``docs/planning/**`` plus the ``.factory``
  targets; N of them burn N threads and N× the OS watch descriptors (a real inotify
  limit) to feed a view that shows exactly one project at a time.
- **Re-root the existing instance in place — rejected.** It means mutating the root,
  unscheduling and rescheduling every watch, and reconciling in-flight debounce state
  against a root it no longer belongs to — a new state machine inside the watcher.
- **Chosen: own at most one watcher and REPLACE it.** ``stop()`` on the old one
  already joins its observer thread, cancels its timers and latches itself stopped;
  the new one is a plain construction on the new root. The swap reuses the watcher's
  existing, tested lifecycle verbatim and adds no state to it.

Watchers are built through an INJECTED ``watcher_factory``, so this service — and the
whole backend layer — still never imports ``watcher_real.py``; only the composition
roots (the CLI, ``create_dev_app``) name the concrete.

**Where the blocking happens, and whose job it is to offload it.** A swap calls
``FileWatcher.stop()``, which for the real watcher joins the observer thread. That
join must never run on the event loop (``ARCHITECTURE.md`` → Cross-cutting,
Concurrency), and the offload deliberately lives at the CALLER — the selection-hook
adapter in :mod:`factory_console.app` wraps :meth:`WatcherSupervisor.retarget` in
``anyio.to_thread.run_sync``. Nothing here awaits or touches a thread pool: keeping
this class synchronous is what lets the lifespan drive it directly and the unit tests
exercise the swap semantics without a loop at all.

**Live updates are opt-in, so a broken swap degrades instead of failing.** A watcher
that cannot be built or started leaves the supervisor watcher-less rather than
half-swapped, and :meth:`retarget` never raises: the request that provoked the switch
is about the SELECTION, and losing the live-refresh stream must not turn a successful
project switch into a 500. The generation counter is what makes that degradation
visible — an SSE connection reads it and ends its stream when it moves (T115), so a
client re-subscribes to the new watcher instead of waiting silently on the old one.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from pathlib import Path

from factory_console.file_adapter.watcher import FileWatcher

_LOGGER = logging.getLogger(__name__)


class WatcherSupervisor:
    """Owns at most one :class:`FileWatcher` and re-points it at the selected root.

    Built by ``create_app`` and driven from two places: the lifespan
    (:meth:`start` / :meth:`stop`, which bound the serving window) and the
    :class:`~factory_console.services.project_selection.SelectionState` on-change hook
    (:meth:`retarget`). Consumers reach the live watcher through
    :func:`~factory_console.api.deps.get_file_watcher`, which reads :meth:`current`.

    **NOT thread-safe, and deliberately unlocked** — the same trade as
    :class:`~factory_console.services.project_selection.SelectionState`. The console
    runs a single uvicorn worker, so the only concurrency here is the one worker thread
    a swap is offloaded to; a lock would serialise nothing the single loop does not
    already serialise, while suggesting concurrent swaps are supported.
    """

    def __init__(
        self,
        watcher_factory: Callable[[Path], FileWatcher] | None,
        *,
        initial: FileWatcher | None = None,
    ) -> None:
        """Bind the factory that builds watchers, and optionally the first one.

        ``watcher_factory`` maps a project root to a fresh, unstarted
        :class:`FileWatcher`. ``None`` is a valid configuration, not a wiring bug: it
        is an app that can hold the watcher it was handed but can never build another,
        which is every pre-v3 composition root and every test that injects a fake.
        Such a supervisor simply becomes watcher-less on the first switch.

        ``initial`` is the already-constructed watcher ``create_app(file_watcher=...)``
        was given. It exists so the pinned boot keeps its exact behaviour: that
        instance — not a fresh one from the factory — is what :meth:`start` starts, so
        the composition root's watcher is the one serving requests, and an app with an
        initial but no factory behaves precisely as it did before this class existed.
        """
        self._factory = watcher_factory
        self._current = initial
        self._root: Path | None = None
        self._generation = 0
        self._started = False

    def current(self) -> FileWatcher | None:
        """Return the live watcher, or ``None`` when there is none right now.

        ``None`` is an ordinary state, not an error: no watcher was ever wired, the
        selection points at nothing, or a swap failed and the supervisor degraded to
        watcher-less. Consumers treat all three the same way the opt-in port always
        required (see :func:`~factory_console.api.deps.get_file_watcher`).
        """
        return self._current

    def generation(self) -> int:
        """Return the monotonic count of swaps performed so far.

        Starts at 0 and increments once per REAL retarget — a same-root no-op does not
        move it, and neither does :meth:`start` or :meth:`stop`. A consumer that
        captured this value alongside a watcher can therefore tell "my watcher is the
        current one" from "my watcher has been replaced" with one integer comparison,
        without holding a reference to a stopped instance to compare against.
        """
        return self._generation

    def start(self, root: Path | None) -> None:
        """Enter the serving window, watching ``root``.

        Called from the lifespan INSIDE the async context, so a ``RealFileWatcher``
        captures the running loop it later hands watchdog callbacks to — exactly where
        the pre-supervisor lifespan called ``start()``.

        A bound ``initial`` watcher is started as-is and the factory is not consulted:
        that instance is already rooted at the boot-time root, and building a second
        watcher for the same directory would double the observer threads and leave the
        composition root's instance permanently unstarted.

        Unlike :meth:`retarget`, a failure here PROPAGATES and fails boot. That is
        today's behaviour and the right one: an operator who wired a watcher and whose
        process cannot start it should be told at startup, not left with a console that
        silently never refreshes.
        """
        self._started = True
        self._root = root
        if self._current is not None:
            self._current.start()
            return
        if self._factory is None or root is None:
            return
        self._current = self._build(self._factory, root)

    def retarget(self, root: Path | None) -> None:
        """Replace the live watcher with one rooted at ``root``. Never raises.

        The on-change hook of
        :meth:`~factory_console.services.project_selection.SelectionState.select`. A
        ``root`` equal to the one already targeted is a no-op — no stop, no generation
        bump, no new watcher — so a re-selection of the current project does not tear
        down a working stream and disconnect every SSE client for nothing.

        Otherwise the old watcher is stopped, the generation is bumped, and a new
        watcher is built and started. ``root is None`` (the selection resolved to no
        path) and a supervisor with no factory both land on the same outcome as a build
        that fails: watcher-less, generation moved. The bump happens even then, because
        what it announces is "the watcher you were reading is gone", which is equally
        true whether a replacement arrived.

        **BLOCKING — must not be called on the event loop.** ``FileWatcher.stop()``
        joins the watcher's observer thread. The selection hook is invoked
        synchronously on the loop thread, so the adapter registered in
        :mod:`factory_console.app` hands this method to ``anyio.to_thread.run_sync``
        rather than calling it inline; see that adapter for the other half of the
        contract.

        Exceptions from the old watcher's ``stop()`` or the new one's construction /
        ``start()`` are logged and swallowed, leaving ``current()`` ``None`` rather
        than a half-swapped pair. A switch that lost its live updates is a degraded
        console; a switch that raised would be a failed request.
        """
        if root == self._root:
            return
        if not self._started:
            # Outside the serving window the lifespan owns the watcher's existence.
            # This is reachable: the swap is dispatched to a worker thread, so one can
            # land after shutdown's ``stop()``, and building a watcher then would leak
            # an observer thread no ``finally`` will ever join.
            return
        self._release("stopping the outgoing watcher for %s failed", self._root)
        self._root = root
        self._generation += 1
        factory = self._factory
        if factory is None or root is None:
            return
        try:
            self._current = self._build(factory, root)
        except Exception as error:
            # ``_root`` keeps naming the failed target: the supervisor IS pointed
            # there, it just has no watcher for it. A later switch away and back
            # retries; re-selecting the same root does not.
            _LOGGER.error("live updates unavailable: no watcher for %s", root, exc_info=error)

    def stop(self) -> None:
        """Leave the serving window and release the watcher. Idempotent.

        Called from the lifespan's ``finally``, so uvicorn's SIGINT/SIGTERM drain
        always joins the observer thread even if serving raised. Safe when nothing is
        current (no watcher was ever wired, or a swap degraded to watcher-less), which
        is what makes it callable unconditionally from that ``finally``.

        A failing ``stop()`` propagates, exactly as the pre-supervisor lifespan let it:
        at shutdown there is no request to protect, and a watcher that could not be
        released is a thread leak worth surfacing rather than a log line at the end of
        a process that is exiting anyway.
        """
        self._started = False
        watcher, self._current = self._current, None
        if watcher is not None:
            watcher.stop()

    def _build(self, factory: Callable[[Path], FileWatcher], root: Path) -> FileWatcher:
        """Construct and start a fresh watcher on ``root``, or raise trying.

        Takes the factory as an argument rather than reading ``self._factory``, so the
        "there is one" check lives with the caller that already had to make it.

        Both steps together, because a constructed-but-unstarted watcher is not a
        useful thing to hand back. When ``start()`` raises, the instance is asked to
        ``stop()`` before the failure propagates — the real watcher may already have
        scheduled watches and spawned its observer thread, and this is the only
        reference to it that will ever exist. A cleanup error is suppressed rather than
        logged: the exception worth reporting is the ``start()`` failure on its way up,
        and a secondary error replacing it would hide the cause behind the symptom.
        """
        watcher = factory(root)
        try:
            watcher.start()
        except Exception:
            with contextlib.suppress(Exception):
                watcher.stop()
            raise
        return watcher

    def _release(self, failure_message: str, *failure_args: object) -> None:
        """Stop and drop the current watcher, logging rather than raising on failure.

        The mid-session half of :meth:`stop`: a swap has to keep going even when the
        outgoing watcher's ``stop()`` misbehaves, or a single bad watcher would pin the
        supervisor to a root the operator has already left.
        """
        watcher, self._current = self._current, None
        if watcher is None:
            return
        try:
            watcher.stop()
        except Exception as error:
            _LOGGER.error(failure_message, *failure_args, exc_info=error)
