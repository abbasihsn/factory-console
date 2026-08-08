"""Integration tests for the FileWatcher lifespan wired into ``create_app`` (T44).

Drive a real app built over a :class:`FakeFileAdapter` through an ASGI lifespan
(FastAPI's ``TestClient`` used as a context manager fires startup on ``__enter__``
and shutdown on ``__exit__``) and pin the watcher seam: an injected
:class:`FileWatcher` is ``start()``-ed on boot and ``stop()``-ed on shutdown, a
``None`` watcher makes the lifespan a clean no-op, and ``get_file_watcher`` reads
back exactly what ``create_app`` bound. Deterministic and I/O-free — the spy
records calls without threads, a clock, or a filesystem.

Since T114 the lifespan drives a
:class:`~factory_console.services.watcher_supervisor.WatcherSupervisor` rather than the
injected watcher directly, so every assertion above is now also the statement that
``create_app(file_watcher=...)`` with NO ``watcher_factory`` behaves exactly as it did
before the supervisor existed. What the supervisor adds is pinned at the end: the
selection hook swaps the watcher, and it does so WITHOUT blocking the event loop the
handler that provoked the switch is running on.
"""

from __future__ import annotations

import threading
import time
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.api.deps import get_file_watcher
from factory_console.app import create_app
from factory_console.domain import Project
from factory_console.domain.watch import ChangeEvent
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.watcher import FileWatcher
from factory_console.services.project_selection import SESSION_PROJECT_ID
from factory_console.store.fake_registry import FakeProjectRegistry


class _SpyFileWatcher:
    """A :class:`FileWatcher` that records the order/count of lifecycle calls.

    A dedicated spy (rather than the flag-flipping ``FakeFileWatcher``) so the
    tests can assert ``start()`` ran before ``yield`` and ``stop()`` after it, and
    that each fired exactly once. ``subscribe`` is never exercised here, so it
    yields an empty stream to stay structurally a ``FileWatcher``.

    ``stop_threads`` and ``start_threads`` record WHICH thread each lifecycle call ran
    on, because the two have OPPOSITE requirements: the real ``stop()`` joins an
    observer thread, so it must stay OFF the event loop, while the real ``start()``
    captures ``asyncio.get_running_loop()``, so it must run ON it. Thread identity is
    the deterministic way to prove a swap honoured both.

    ``stop_delay`` stands in for the observer join a real ``stop()`` performs. It is what
    makes an overlapping-swap test meaningful: with the release taking no time at all, two
    swaps would serialise by luck rather than by the lock that is supposed to serialise
    them, and the test would pass against the bug.
    """

    def __init__(self, stop_delay: float = 0.0) -> None:
        self.calls: list[str] = []
        self.stop_threads: list[int] = []
        self.start_threads: list[int] = []
        self._stop_delay = stop_delay

    @property
    def running(self) -> bool:
        """Whether this watcher has been started more times than it has been stopped."""
        return self.calls.count("start") > self.calls.count("stop")

    def start(self) -> None:
        self.calls.append("start")
        self.start_threads.append(threading.get_ident())

    def stop(self) -> None:
        if self._stop_delay:
            time.sleep(self._stop_delay)
        self.calls.append("stop")
        self.stop_threads.append(threading.get_ident())

    def subscribe(self) -> AsyncIterator[ChangeEvent]:  # pragma: no cover - unused here
        raise NotImplementedError("the lifespan tests never subscribe")


def _make_app(
    file_watcher: FileWatcher | None,
    *,
    watcher_factory: Callable[[Path], FileWatcher] | None = None,
    project_registry: FakeProjectRegistry | None = None,
) -> FastAPI:
    """Build the real app over an empty FakeFileAdapter with the given watcher.

    ``project_registry`` stays ``None`` for most cases (pinned mode, where the only
    resolvable id is the session sentinel). A registry is needed only to make ``select()``
    resolve TWO distinct non-``None`` roots, which is what an overlapping-swap test
    requires — with one root, one of the two swaps builds nothing and there is no second
    watcher to orphan.
    """
    project = Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        discoveredAt=datetime(2026, 1, 1),
    )
    return create_app(
        FakeFileAdapter(project=project, tickets=[]),
        version="0.0.0",
        project_root=Path("/proj"),
        file_watcher=file_watcher,
        watcher_factory=watcher_factory,
        project_registry=project_registry,
    )


def test_lifespan_starts_and_stops_the_injected_watcher() -> None:
    # isinstance holds because FileWatcher is @runtime_checkable (structural).
    watcher = _SpyFileWatcher()
    assert isinstance(watcher, FileWatcher)
    app = _make_app(watcher)

    # Entering the TestClient context runs the lifespan startup; start() fires and
    # stop() has not yet.
    with TestClient(app) as client:
        assert watcher.calls == ["start"]
        assert client.get("/api/v1/health").status_code == 200
        # The supervisor holds the injected instance, so the DI seam still hands the
        # SSE endpoint exactly the watcher the composition root wired.
        assert get_file_watcher(SimpleNamespace(app=app)) is watcher  # type: ignore[arg-type]

    # Exiting the context runs the lifespan shutdown; stop() fires exactly once.
    assert watcher.calls == ["start", "stop"]


def test_lifespan_is_a_clean_no_op_without_a_watcher() -> None:
    app = _make_app(None)
    # No watcher bound: the lifespan must start/serve/shut down without raising.
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200


def test_stop_runs_even_when_serving_would_raise() -> None:
    # The lifespan stops the watcher in a ``finally``, so an error while serving
    # still releases the watcher on shutdown rather than leaking the observer.
    watcher = _SpyFileWatcher()
    app = _make_app(watcher)

    @app.get("/boom")
    def _boom() -> None:
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/boom").status_code == 500

    assert watcher.calls == ["start", "stop"]


# How long a spy's ``stop()`` blocks when it is standing in for a real observer join.
# Long enough that two unserialised swaps WOULD overlap (the second release would start
# while the first is still joining), short enough not to slow the suite. Only the
# overlapping-swap test needs it; every other spy stops instantly.
_SLOW_STOP = 0.05


def _wait_for(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    """Block the TEST thread until ``predicate`` holds, bounded.

    The swap is fire-and-forget: the selection hook schedules it and returns, so the
    response can arrive before the watcher has been released. Polling (rather than a
    fixed sleep) keeps the test fast when the swap lands immediately and non-flaky when
    the machine is loaded; the bound turns a swap that never happens into a failed
    assertion below rather than a hung suite.
    """
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)


def _wait_until_stopped(watcher: _SpyFileWatcher, *, timeout: float = 2.0) -> None:
    """Block the TEST thread until the app's loop has released ``watcher``."""
    _wait_for(lambda: "stop" in watcher.calls, timeout=timeout)


def _wait_until_swapped(app: FastAPI, generation: int, *, timeout: float = 2.0) -> None:
    """Block the TEST thread until the supervisor's generation reaches ``generation``.

    The right signal to wait on, where ``_wait_until_stopped`` is not. A swap is TWO
    phases: ``retarget_release`` stops the outgoing watcher OFF the loop (blocking, so
    the join cannot stall every request and SSE stream), then ``retarget_rebuild`` runs
    back ON the loop, bumps the generation and builds the successor. Observing ``stop``
    only proves phase one finished — phase two is still in flight, so anything the test
    asserts about the generation or the successor is a race it can lose.

    And did: this suite passed on 3.11 and failed on 3.12 with ``assert 0 == 1``,
    purely on which side of that hop the test thread happened to land.

    The generation is the correct signal because it is the LAST thing the swap settles.
    """
    _wait_for(lambda: app.state.watcher_supervisor.generation() >= generation, timeout=timeout)


def test_a_selection_change_releases_the_outgoing_watcher_off_the_loop_thread() -> None:
    # The app-level hook that T114 registers on SelectionState: a switch must release
    # the outgoing watcher, and the thread join that releasing it performs must not run
    # on the event loop, where it would stall every other request and SSE stream.
    watcher = _SpyFileWatcher()
    app = _make_app(watcher)
    handler_threads: list[int] = []

    @app.post("/switch")
    async def _switch() -> None:
        # ``async def`` on purpose: this runs ON the loop thread, exactly like the
        # handler that will call ``select()`` for real.
        handler_threads.append(threading.get_ident())
        app.state.selection.select(None)

    with TestClient(app) as client:
        assert client.post("/switch").status_code == 200
        _wait_until_stopped(watcher)
        # Both halves of the swap, not just the release — see `_wait_until_swapped`.
        _wait_until_swapped(app, 1)

        assert watcher.calls == ["start", "stop"]
        # No factory was wired, so the pinned app cannot build a successor: it degrades
        # to watcher-less, and the DI seam reports that rather than a stopped watcher.
        assert app.state.watcher_supervisor.current() is None
        assert get_file_watcher(SimpleNamespace(app=app)) is None  # type: ignore[arg-type]
        assert app.state.watcher_supervisor.generation() == 1
        # The join ran in a worker thread, not the one that served the request.
        assert watcher.stop_threads[0] != handler_threads[0]

    # Shutdown is still clean with nothing left to release, and does not stop twice.
    assert watcher.calls == ["start", "stop"]


def test_a_selection_change_starts_the_incoming_watcher_on_the_loop_thread() -> None:
    # The other half of the same invariant, and the one a worker-thread swap cannot
    # satisfy: ``RealFileWatcher.start()`` captures ``asyncio.get_running_loop()``, so a
    # build dispatched to a worker thread raises there, is swallowed, and leaves a real
    # project switch permanently without live updates. Only a wired factory reaches this
    # path, since the pinned app has no successor to build.
    outgoing = _SpyFileWatcher()
    built: list[_SpyFileWatcher] = []

    def _factory(root: Path) -> FileWatcher:
        incoming = _SpyFileWatcher()
        built.append(incoming)
        return incoming

    app = _make_app(outgoing, watcher_factory=_factory)
    loop_threads: list[int] = []

    @app.post("/switch")
    async def _switch(project_id: str | None = None) -> None:
        # ``async def`` on purpose: this runs ON the loop thread, exactly like the
        # handler that will call ``select()`` for real.
        loop_threads.append(threading.get_ident())
        app.state.selection.select(project_id)

    with TestClient(app) as client:
        # Away from the pinned root first (nothing to build), then back to it — two
        # requests rather than two selects in one, so the second swap is scheduled only
        # after the first has landed and the roots cannot be applied out of order.
        assert client.post("/switch").status_code == 200
        _wait_until_stopped(outgoing)
        # BOTH halves, or the comment above is not true: `stop` is only phase one, so
        # waiting on it alone would schedule the second swap while the first is still
        # rebuilding — the very out-of-order application this two-request shape exists
        # to prevent.
        _wait_until_swapped(app, 1)
        assert client.post("/switch", params={"project_id": SESSION_PROJECT_ID}).status_code == 200
        _wait_for(lambda: bool(built))

        assert len(built) == 1
        incoming = built[0]
        assert incoming.calls == ["start"]
        assert app.state.watcher_supervisor.current() is incoming
        assert app.state.watcher_supervisor.generation() == 2
        # The successor was started on the loop thread, where a real watcher finds the
        # running loop it hands watchdog callbacks to...
        assert incoming.start_threads == [loop_threads[-1]]
        # ...while the outgoing watcher's blocking join stayed off it.
        assert outgoing.stop_threads[0] not in loop_threads


def test_overlapping_selection_changes_orphan_no_watcher(tmp_path: Path) -> None:
    # The switch hook is fire-and-forget, so two selects close together (a double-clicked
    # switcher, two tabs) put two swaps in flight — and a swap is NOT atomic: it releases
    # on a worker thread and rebuilds back on the loop. Unserialised, both releases run
    # first, the second stops nothing because ``current`` is already ``None``, and then
    # both rebuilds run with the second overwriting the first's watcher WITHOUT stopping
    # it. That watcher is then unreachable: shutdown's ``stop()`` only ever sees the
    # current one, so its observer thread is never joined. The invariant that catches it
    # is simply that at most one watcher is ever running.
    registry = FakeProjectRegistry()
    row_a = registry.add_project(tmp_path / "a")
    row_b = registry.add_project(tmp_path / "b")
    outgoing = _SpyFileWatcher(stop_delay=_SLOW_STOP)
    built: list[_SpyFileWatcher] = []

    def _factory(root: Path) -> FileWatcher:
        incoming = _SpyFileWatcher(stop_delay=_SLOW_STOP)
        built.append(incoming)
        return incoming

    app = _make_app(outgoing, watcher_factory=_factory, project_registry=registry)

    @app.post("/switch-twice")
    async def _switch_twice() -> None:
        # Both selects in ONE handler with no await between them, so the second swap task
        # is created before the first has run any of its own body. Two distinct roots, so
        # both swaps really build.
        app.state.selection.select(row_a.id)
        app.state.selection.select(row_b.id)

    supervisor = app.state.watcher_supervisor
    with TestClient(app) as client:
        assert client.post("/switch-twice").status_code == 200
        _wait_for(lambda: supervisor.generation() == 2 and len(built) == 2)

        # Exactly one watcher is running, and it is the one the DI seam hands out. Before
        # the swap was serialised this list had two entries: the live one, and the
        # orphan nothing could reach.
        assert [w for w in (outgoing, *built) if w.running] == [supervisor.current()]
        assert outgoing.calls.count("stop") == 1

    # Shutdown joins the one live watcher, leaving nothing running at all.
    assert not [w for w in (outgoing, *built) if w.running]


def test_a_selection_change_with_no_running_loop_swaps_inline() -> None:
    # ``select()`` called from a plain synchronous caller — no server, no loop. There is
    # no event loop for the join to stall, so the hook must do the swap inline rather
    # than raise trying to schedule it onto a loop that does not exist.
    watcher = _SpyFileWatcher()
    app = _make_app(watcher)
    supervisor = app.state.watcher_supervisor
    supervisor.start(Path("/proj"))

    app.state.selection.select(None)

    assert watcher.calls == ["start", "stop"]
    assert watcher.stop_threads == [threading.get_ident()]
    assert supervisor.current() is None
