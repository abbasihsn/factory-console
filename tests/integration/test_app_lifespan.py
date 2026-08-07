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
from collections.abc import AsyncIterator
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


class _SpyFileWatcher:
    """A :class:`FileWatcher` that records the order/count of lifecycle calls.

    A dedicated spy (rather than the flag-flipping ``FakeFileWatcher``) so the
    tests can assert ``start()`` ran before ``yield`` and ``stop()`` after it, and
    that each fired exactly once. ``subscribe`` is never exercised here, so it
    yields an empty stream to stay structurally a ``FileWatcher``.

    ``stop_threads`` records WHICH thread each release ran on, because the real
    ``stop()`` joins an observer thread: that is the call the swap has to keep off the
    event loop, and the thread identity is the deterministic way to prove it did.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.stop_threads: list[int] = []

    def start(self) -> None:
        self.calls.append("start")

    def stop(self) -> None:
        self.calls.append("stop")
        self.stop_threads.append(threading.get_ident())

    def subscribe(self) -> AsyncIterator[ChangeEvent]:  # pragma: no cover - unused here
        raise NotImplementedError("the lifespan tests never subscribe")


def _make_app(file_watcher: FileWatcher | None) -> FastAPI:
    """Build the real app over an empty FakeFileAdapter with the given watcher."""
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


def _wait_until_stopped(watcher: _SpyFileWatcher, *, timeout: float = 2.0) -> None:
    """Block the TEST thread until the app's loop has completed the swap, bounded.

    The swap is fire-and-forget: the selection hook schedules it and returns, so the
    response can arrive before the watcher has been released. Polling (rather than a
    fixed sleep) keeps the test fast when the swap lands immediately and non-flaky when
    the machine is loaded; the bound turns a swap that never happens into a failed
    assertion below rather than a hung suite.
    """
    deadline = time.monotonic() + timeout
    while "stop" not in watcher.calls and time.monotonic() < deadline:
        time.sleep(0.01)


def test_a_selection_change_swaps_the_watcher_off_the_loop_thread() -> None:
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
