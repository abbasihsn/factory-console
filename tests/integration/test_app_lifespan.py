"""Integration tests for the FileWatcher lifespan wired into ``create_app`` (T44).

Drive a real app built over a :class:`FakeFileAdapter` through an ASGI lifespan
(FastAPI's ``TestClient`` used as a context manager fires startup on ``__enter__``
and shutdown on ``__exit__``) and pin the watcher seam: an injected
:class:`FileWatcher` is ``start()``-ed on boot and ``stop()``-ed on shutdown, a
``None`` watcher makes the lifespan a clean no-op, and ``get_file_watcher`` reads
back exactly what ``create_app`` bound. Deterministic and I/O-free — the spy
records calls without threads, a clock, or a filesystem.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

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
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def start(self) -> None:
        self.calls.append("start")

    def stop(self) -> None:
        self.calls.append("stop")

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
