"""Integration tests for ``GET /api/v1/events`` (the live-change SSE endpoint).

Pin the endpoint's contract end to end WITHOUT a network transport: an infinite
``text/event-stream`` body cannot be read back through ``httpx.ASGITransport`` (it
buffers the whole response before returning, so it deadlocks on a stream that
never ends — verified against this repo's httpx) nor through ``TestClient`` for
the same reason. Instead these tests drive the real route handler and the
:func:`sse_event_stream` async generator directly, which is fully deterministic
(no threads, no clock, no buffering) and exercises exactly the same code path a
browser's ``EventSource`` would.

The assertions cover the ticket's verification set: the route is published on the
frozen OpenAPI schema; the handler returns a ``text/event-stream`` response with
the ``no-cache`` / ``X-Accel-Buffering: no`` headers and streams the initial
``ready`` frame; a watcher-emitted :class:`ChangeEvent` arrives as a well-formed
``event: change`` frame whose ``data:`` JSON carries the camelCase fields;
disconnecting (closing the stream) drains the hub's subscriber list back to empty
(the leak-safety guarantee); and the ``watcher=None`` path degrades to ``ready``
plus heartbeat comments. Heartbeats use a tiny interval so everything stays
sub-second. The repo runs ``asyncio_mode=auto`` so ``async def test_...`` needs no
decorator.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.api.v1.events import events
from factory_console.app import create_app
from factory_console.domain import Project
from factory_console.domain.watch import ChangeEvent
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.watcher import FakeFileWatcher
from factory_console.services.events_service import sse_event_stream

_FAKE_PROJECT = Project(
    rootPath=Path("/factory/demo-project"),
    ticketsManifestPath=Path("/factory/demo-project/docs/planning/tickets.json"),
    ticketsDir=Path("/factory/demo-project/docs/planning/tickets"),
    discoveredAt=datetime(2026, 7, 21, 12, 30, 0),
)

_READY_FRAME = "event: ready\ndata: {}\n\n"
_KEEPALIVE_FRAME = ": keepalive\n\n"
_CHANGE_PREFIX = "event: change\ndata: "


def _make_event(kind: str = "modified", path: str = "docs/planning/tickets.json") -> ChangeEvent:
    return ChangeEvent(kind=kind, path=path, scope="planning", at=datetime(2026, 7, 24, 12, 0, 0))


class _FakeRequest:
    """Minimal stand-in for a Starlette ``Request`` exposing ``is_disconnected``.

    Reports "connected" for the first ``disconnect_after`` checks and "disconnected"
    thereafter, so the stream terminates deterministically after a bounded number
    of heartbeats instead of running forever.
    """

    def __init__(self, disconnect_after: int) -> None:
        self._checks = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._checks += 1
        return self._checks > self._disconnect_after


class _EmptyWatcher:
    """A ``FileWatcher`` whose subscription ends immediately (no events, ever).

    Exercises the stream's ``StopAsyncIteration`` branch — the subscriber
    generator returning — which a fan-out watcher never triggers on its own.
    """

    def start(self) -> None:  # pragma: no cover - unused
        ...

    def stop(self) -> None:  # pragma: no cover - unused
        ...

    def subscribe(self) -> AsyncIterator[ChangeEvent]:
        return self._empty()

    async def _empty(self) -> AsyncIterator[ChangeEvent]:
        return
        yield  # pragma: no cover - marks this a generator; never reached


def _make_app(file_watcher: object | None) -> FastAPI:
    """Build the real app over an empty FakeFileAdapter with the given watcher."""
    return create_app(
        FakeFileAdapter(project=_FAKE_PROJECT, tickets=[]),
        version="0.0.0",
        project_root=Path("/factory/demo-project"),
        file_watcher=file_watcher,
    )


async def _poll_until(
    predicate: Callable[[], bool], *, timeout: float = 2.0, step: float = 0.01
) -> None:
    """Await ``predicate()`` becoming true, bounded — keeps async cleanup deterministic."""
    elapsed = 0.0
    while not predicate() and elapsed < timeout:
        await asyncio.sleep(step)
        elapsed += step


# --------------------------------------------------------------------------- #
# Route registration (OpenAPI is a plain JSON response — safe to read fully)
# --------------------------------------------------------------------------- #


def test_openapi_publishes_events_path() -> None:
    with TestClient(_make_app(FakeFileWatcher())) as client:
        schema = client.get("/api/v1/openapi.json").json()
    assert "/api/v1/events" in schema["paths"]
    assert "events" in schema["paths"]["/api/v1/events"]["get"]["tags"]


# --------------------------------------------------------------------------- #
# Handler: text/event-stream response, ready + change frames, release on close
# --------------------------------------------------------------------------- #


async def test_handler_streams_ready_then_change_and_releases_on_close() -> None:
    watcher = FakeFileWatcher()
    request = _FakeRequest(disconnect_after=10)

    # Call the real route handler; it wraps sse_event_stream in a StreamingResponse.
    resp = await events(request, watcher)  # type: ignore[arg-type]
    assert resp.media_type == "text/event-stream"
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["x-accel-buffering"] == "no"

    body = resp.body_iterator
    # First frame is the ready handshake — emitted before any subscription.
    assert await anext(body) == _READY_FRAME

    # Advancing the stream is what subscribes; schedule it, wait for the queue to
    # register, THEN emit so the fan-out is not dropped.
    next_frame = asyncio.ensure_future(anext(body))
    await _poll_until(lambda: bool(watcher._hub._subscribers))
    event = _make_event(kind="created", path="ROADMAP.md")
    watcher.emit(event)

    frame = await asyncio.wait_for(next_frame, 1.0)
    assert frame.startswith(_CHANGE_PREFIX)
    data = json.loads(frame[len(_CHANGE_PREFIX) :].strip())
    # Fields are natively camelCase (all single lowercase words).
    assert set(data) == {"kind", "path", "scope", "at"}
    assert data["kind"] == "created"
    assert data["path"] == "ROADMAP.md"
    assert data["scope"] == "planning"
    assert data["at"] == "2026-07-24T12:00:00"

    # A second change flows over the SAME long-lived subscription (the stream loops
    # back and re-awaits the next event rather than re-subscribing per event).
    watcher.emit(_make_event(kind="deleted", path="docs/planning/tickets.json"))
    second = await asyncio.wait_for(anext(body), 1.0)
    assert json.loads(second[len(_CHANGE_PREFIX) :].strip())["kind"] == "deleted"

    # Closing the stream (client disconnect) runs the generator's finally, which
    # releases the subscription. White-box leak check: the hub drains to empty.
    await body.aclose()
    assert watcher._hub._subscribers == []


# --------------------------------------------------------------------------- #
# watcher=None -> ready + heartbeat-only, no crash (direct generator drive)
# --------------------------------------------------------------------------- #


async def test_watcher_none_yields_ready_then_heartbeat() -> None:
    request = _FakeRequest(disconnect_after=1)
    stream = sse_event_stream(None, request, heartbeat_interval=0.01)  # type: ignore[arg-type]
    try:
        assert await anext(stream) == _READY_FRAME
        assert await anext(stream) == _KEEPALIVE_FRAME
        # After one keepalive the fake reports disconnected, so the stream ends.
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    finally:
        await stream.aclose()


# --------------------------------------------------------------------------- #
# Heartbeat + disconnect on the watcher path (direct generator drive)
# --------------------------------------------------------------------------- #


async def test_watcher_present_heartbeats_then_stops_on_disconnect() -> None:
    watcher = FakeFileWatcher()
    request = _FakeRequest(disconnect_after=1)
    stream = sse_event_stream(watcher, request, heartbeat_interval=0.01)  # type: ignore[arg-type]
    try:
        assert await anext(stream) == _READY_FRAME
        # No event emitted: the first heartbeat times out and emits a keepalive,
        # the second sees the fake disconnect and ends the stream.
        assert await anext(stream) == _KEEPALIVE_FRAME
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    finally:
        await stream.aclose()
    # The subscription was released in the finally.
    assert watcher._hub._subscribers == []


# --------------------------------------------------------------------------- #
# Subscriber stream ending on its own -> StopAsyncIteration branch
# --------------------------------------------------------------------------- #


async def test_stream_stops_when_subscription_ends() -> None:
    request = _FakeRequest(disconnect_after=10)
    stream = sse_event_stream(_EmptyWatcher(), request)  # type: ignore[arg-type]
    try:
        assert await anext(stream) == _READY_FRAME
        # The subscriber generator returns immediately -> StopAsyncIteration -> stop.
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    finally:
        await stream.aclose()
