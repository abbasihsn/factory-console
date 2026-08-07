"""Unit tests for :class:`WatcherSupervisor`'s swap semantics (T114).

Deterministic and thread-free: a spy watcher records its own lifecycle calls and a
recording factory hands out one per root, so every assertion is about WHICH watcher is
current, how often each was stopped, and where the generation counter stands — never
about a real observer thread. That is the point of the injected factory seam: the swap
logic is testable without ``watchdog``, a filesystem, or an event loop.

The failure paths are covered as deliberately as the happy one, because degrading to
watcher-less (rather than raising, or keeping a stopped watcher current) is the
supervisor's contract, not an implementation detail: live updates are opt-in and a
project switch must not fail because the new project's watcher would not start.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from factory_console.domain.watch import ChangeEvent
from factory_console.file_adapter.watcher import FileWatcher
from factory_console.services.watcher_supervisor import WatcherSupervisor

_ROOT_A = Path("/projects/alpha")
_ROOT_B = Path("/projects/beta")


class _SpyWatcher:
    """A :class:`FileWatcher` that counts its own ``start``/``stop`` calls.

    Counters rather than a boolean flag (unlike ``FakeFileWatcher``) because the
    invariant under test is "stopped EXACTLY once per swap": a flag cannot tell a
    single stop from a double stop, and a watcher stopped twice would double-join a
    real observer thread. ``fails_to_start`` drives the degrade-on-failure paths, and
    ``subscribe`` is never exercised here.
    """

    def __init__(self, root: Path, *, fails_to_start: bool = False) -> None:
        self.root = root
        self.fails_to_start = fails_to_start
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        self.starts += 1
        if self.fails_to_start:
            raise RuntimeError(f"cannot watch {self.root}")

    def stop(self) -> None:
        self.stops += 1

    def subscribe(self) -> AsyncIterator[ChangeEvent]:  # pragma: no cover - unused here
        raise NotImplementedError("the supervisor tests never subscribe")


class _RecordingFactory:
    """Builds one :class:`_SpyWatcher` per call and keeps every one it built.

    Retaining the whole list (not just the latest) is what lets a test assert an
    OUTGOING watcher was stopped after the supervisor has already dropped its
    reference to it.
    """

    def __init__(self, *, fails_to_start: bool = False, raises: bool = False) -> None:
        self.fails_to_start = fails_to_start
        self.raises = raises
        self.built: list[_SpyWatcher] = []

    def __call__(self, root: Path) -> _SpyWatcher:
        if self.raises:
            raise OSError(f"cannot construct a watcher for {root}")
        watcher = _SpyWatcher(root, fails_to_start=self.fails_to_start)
        self.built.append(watcher)
        return watcher


def _started_supervisor(factory: _RecordingFactory) -> WatcherSupervisor:
    """Return a supervisor inside its serving window, watching ``_ROOT_A``."""
    supervisor = WatcherSupervisor(factory)
    supervisor.start(_ROOT_A)
    return supervisor


def test_start_builds_and_starts_a_watcher_for_the_initial_root() -> None:
    factory = _RecordingFactory()
    supervisor = _started_supervisor(factory)

    assert [watcher.root for watcher in factory.built] == [_ROOT_A]
    assert supervisor.current() is factory.built[0]
    assert factory.built[0].starts == 1
    # Nothing has been swapped yet, so the counter is still at its origin.
    assert supervisor.generation() == 0


def test_start_uses_the_initial_watcher_instead_of_the_factory() -> None:
    # The pinned boot path: create_app(file_watcher=...) hands over an instance already
    # rooted at the boot root, so building a second one would double the observer
    # threads and leave the composition root's watcher unstarted forever.
    initial = _SpyWatcher(_ROOT_A)
    assert isinstance(initial, FileWatcher)  # structural: FileWatcher is runtime_checkable
    factory = _RecordingFactory()
    supervisor = WatcherSupervisor(factory, initial=initial)

    supervisor.start(_ROOT_A)

    assert supervisor.current() is initial
    assert initial.starts == 1
    assert factory.built == []


def test_retarget_swaps_the_watcher_and_bumps_the_generation() -> None:
    factory = _RecordingFactory()
    supervisor = _started_supervisor(factory)
    first = factory.built[0]

    supervisor.retarget(_ROOT_B)

    second = factory.built[1]
    assert supervisor.current() is second
    assert second.root == _ROOT_B
    assert second.starts == 1
    # The outgoing watcher is stopped exactly once — a second stop would double-join
    # the real watcher's observer thread.
    assert first.stops == 1
    assert supervisor.generation() == 1


def test_generation_increments_once_per_real_swap() -> None:
    factory = _RecordingFactory()
    supervisor = _started_supervisor(factory)

    supervisor.retarget(_ROOT_B)
    supervisor.retarget(_ROOT_A)
    supervisor.retarget(_ROOT_B)

    assert supervisor.generation() == 3
    assert [watcher.root for watcher in factory.built] == [_ROOT_A, _ROOT_B, _ROOT_A, _ROOT_B]


def test_retarget_to_the_same_root_is_a_no_op() -> None:
    factory = _RecordingFactory()
    supervisor = _started_supervisor(factory)
    watcher = factory.built[0]

    supervisor.retarget(_ROOT_A)

    # Re-selecting the project already being watched must not tear down a working
    # stream: same instance, never stopped, no new watcher, counter unmoved.
    assert supervisor.current() is watcher
    assert watcher.stops == 0
    assert len(factory.built) == 1
    assert supervisor.generation() == 0


def test_retarget_to_none_releases_the_current_watcher() -> None:
    factory = _RecordingFactory()
    supervisor = _started_supervisor(factory)
    watcher = factory.built[0]

    supervisor.retarget(None)

    # A selection that resolves to no path leaves nothing to watch — but the counter
    # still moves, because "the watcher you were reading is gone" is what it announces.
    assert supervisor.current() is None
    assert watcher.stops == 1
    assert len(factory.built) == 1
    assert supervisor.generation() == 1


def test_retarget_without_a_factory_releases_and_cannot_rebuild() -> None:
    # The pre-v3 wiring: an injected watcher and no factory. Switching away from the
    # pinned root can only end watcher-less, and that must not raise.
    initial = _SpyWatcher(_ROOT_A)
    supervisor = WatcherSupervisor(None, initial=initial)
    supervisor.start(_ROOT_A)

    supervisor.retarget(_ROOT_B)

    assert supervisor.current() is None
    assert initial.stops == 1
    assert supervisor.generation() == 1


def test_retarget_leaves_no_watcher_when_construction_raises() -> None:
    factory = _RecordingFactory(raises=True)
    initial = _SpyWatcher(_ROOT_A)
    supervisor = WatcherSupervisor(factory, initial=initial)
    supervisor.start(_ROOT_A)

    # Never raises: the request that provoked the switch is about the selection, and
    # losing live updates must not turn a successful switch into a 500.
    supervisor.retarget(_ROOT_B)

    assert supervisor.current() is None
    # The old watcher is stopped and NOT left current — a stopped watcher handed to the
    # SSE endpoint would look live while emitting nothing, forever.
    assert initial.stops == 1
    assert supervisor.generation() == 1


def test_retarget_leaves_no_watcher_when_the_new_watcher_fails_to_start() -> None:
    factory = _RecordingFactory(fails_to_start=True)
    initial = _SpyWatcher(_ROOT_A)
    supervisor = WatcherSupervisor(factory, initial=initial)
    supervisor.start(_ROOT_A)

    supervisor.retarget(_ROOT_B)

    assert supervisor.current() is None
    assert initial.stops == 1
    # The half-started instance is released rather than abandoned: it may already have
    # scheduled watches, and this was the only reference to it.
    assert factory.built[0].stops == 1
    assert supervisor.generation() == 1


def test_a_later_switch_recovers_from_a_failed_swap() -> None:
    # Degrading to watcher-less is not terminal: the supervisor holds no wreckage from
    # the failed swap, so the next switch (to a root whose watcher does build) is a
    # plain build with nothing to stop first.
    factory = _RecordingFactory(raises=True)
    supervisor = WatcherSupervisor(factory, initial=_SpyWatcher(_ROOT_A))
    supervisor.start(_ROOT_A)
    supervisor.retarget(_ROOT_B)
    assert supervisor.current() is None

    factory.raises = False
    supervisor.retarget(_ROOT_A)

    assert supervisor.current() is factory.built[0]
    assert factory.built[0].root == _ROOT_A
    assert factory.built[0].starts == 1
    assert supervisor.generation() == 2


def test_retarget_survives_an_outgoing_watcher_that_fails_to_stop() -> None:
    class _UnstoppableWatcher(_SpyWatcher):
        def stop(self) -> None:
            super().stop()
            raise RuntimeError("the observer thread would not join")

    factory = _RecordingFactory()
    outgoing = _UnstoppableWatcher(_ROOT_A)
    supervisor = WatcherSupervisor(factory, initial=outgoing)
    supervisor.start(_ROOT_A)

    supervisor.retarget(_ROOT_B)

    # One misbehaving watcher must not pin the supervisor to a root the operator has
    # already left, so the swap completes around it.
    assert outgoing.stops == 1
    assert supervisor.current() is factory.built[0]
    assert supervisor.generation() == 1


def test_retarget_after_stop_builds_nothing() -> None:
    # A swap is dispatched to a worker thread, so one can land after the lifespan's
    # stop(). Building then would leak an observer thread nothing will ever join.
    factory = _RecordingFactory()
    supervisor = _started_supervisor(factory)
    supervisor.stop()

    supervisor.retarget(_ROOT_B)

    assert supervisor.current() is None
    assert len(factory.built) == 1


def test_retarget_release_reports_whether_the_swap_is_really_happening() -> None:
    # The two halves exist because their thread requirements are opposite: the app hook
    # runs the release in a worker thread (it joins) and the rebuild back on the loop (a
    # real watcher captures the running loop in ``start()``). The boolean is what tells
    # the caller a rebuild is owed at all.
    factory = _RecordingFactory()
    supervisor = _started_supervisor(factory)

    assert supervisor.retarget_release(_ROOT_A) is False
    assert factory.built[0].stops == 0

    assert supervisor.retarget_release(_ROOT_B) is True
    assert factory.built[0].stops == 1
    # The release decides and stops; only the rebuild moves the counter and builds.
    assert supervisor.generation() == 0
    assert len(factory.built) == 1

    supervisor.retarget_rebuild(_ROOT_B)

    assert supervisor.current() is factory.built[1]
    assert factory.built[1].starts == 1
    assert supervisor.generation() == 1


def test_retarget_release_reports_no_swap_outside_the_serving_window() -> None:
    factory = _RecordingFactory()
    supervisor = _started_supervisor(factory)
    supervisor.stop()

    assert supervisor.retarget_release(_ROOT_B) is False


def test_retarget_rebuild_builds_nothing_when_stop_lands_between_the_halves() -> None:
    # Shutdown racing an in-flight swap: the release ran in a worker thread, the
    # lifespan's stop() then ran on the loop, and the rebuild resumes afterwards.
    # Building then would leak an observer thread nothing will ever join.
    factory = _RecordingFactory()
    supervisor = _started_supervisor(factory)
    assert supervisor.retarget_release(_ROOT_B) is True
    supervisor.stop()

    supervisor.retarget_rebuild(_ROOT_B)

    assert supervisor.current() is None
    assert len(factory.built) == 1
    assert supervisor.generation() == 0


def test_stop_is_idempotent() -> None:
    factory = _RecordingFactory()
    supervisor = _started_supervisor(factory)
    watcher = factory.built[0]

    supervisor.stop()
    supervisor.stop()

    assert supervisor.current() is None
    # Stopped once despite two calls: the lifespan's ``finally`` may run alongside an
    # explicit shutdown, and a double stop double-joins a real observer thread.
    assert watcher.stops == 1


def test_stop_without_a_current_watcher_does_not_raise() -> None:
    # The adapter-only app: no watcher, no factory. The lifespan calls stop()
    # unconditionally, so this is the common path, not an edge case.
    supervisor = WatcherSupervisor(None)
    supervisor.start(_ROOT_A)

    supervisor.stop()

    assert supervisor.current() is None
    assert supervisor.generation() == 0


def test_start_propagates_a_failure_to_start_the_initial_watcher() -> None:
    # Unlike a mid-session swap, a boot failure is worth failing boot over: an operator
    # who wired a watcher should be told at startup, not handed a console that silently
    # never refreshes.
    initial = _SpyWatcher(_ROOT_A, fails_to_start=True)
    supervisor = WatcherSupervisor(None, initial=initial)

    with pytest.raises(RuntimeError, match="cannot watch"):
        supervisor.start(_ROOT_A)
