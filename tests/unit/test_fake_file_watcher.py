"""Unit tests for the deterministic :class:`FakeFileWatcher` + ``ChangeEvent``.

These pin the long-lived ``FileWatcher`` port the backend SSE endpoint codes
against before the watchdog-backed watcher exists: the ``@runtime_checkable``
``isinstance`` gate, the ``start()``/``stop()`` idempotence, fan-out to
independent per-client subscribers, and leak-safe unregistration on
cancellation. Deterministic and I/O-free — no filesystem, no watchdog, no clock;
``asyncio.wait_for`` with a small timeout keeps any hang a fast failure. The repo
runs ``asyncio_mode=auto`` so ``async def test_...`` needs no decorator.
"""

import asyncio
from datetime import datetime

import pytest
from pydantic import ValidationError

from factory_console.domain.watch import ChangeEvent
from factory_console.file_adapter.watcher import FakeFileWatcher, FileWatcher

# A short timeout so a wrong implementation fails fast instead of hanging.
_TIMEOUT = 1.0


def _make_event(kind: str = "modified", path: str = "docs/planning/tickets.json") -> ChangeEvent:
    return ChangeEvent(
        kind=kind,
        path=path,
        scope="planning",
        at=datetime(2026, 7, 24, 12, 0, 0),
    )


class _PartialWatcher:
    """Implements only two of the three methods — proves the runtime check is real."""

    def start(self) -> None:  # pragma: no cover - never called
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - never called
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# runtime_checkable Protocol gate
# --------------------------------------------------------------------------- #


def test_fake_satisfies_runtime_checkable_file_watcher() -> None:
    assert isinstance(FakeFileWatcher(), FileWatcher)


def test_object_without_the_three_methods_is_not_a_file_watcher() -> None:
    # The runtime check is real, not vacuous: a bare object and a partial
    # implementation (missing subscribe) are both rejected.
    assert not isinstance(object(), FileWatcher)
    assert not isinstance(_PartialWatcher(), FileWatcher)


# --------------------------------------------------------------------------- #
# start() / stop() idempotence
# --------------------------------------------------------------------------- #


def test_start_and_stop_are_idempotent() -> None:
    watcher = FakeFileWatcher()
    watcher.start()
    watcher.start()  # second call is safe
    assert watcher._running is True
    watcher.stop()
    watcher.stop()  # second call is safe
    assert watcher._running is False


# --------------------------------------------------------------------------- #
# subscribe() / emit() fan-out
# --------------------------------------------------------------------------- #


async def test_subscribe_receives_event_emitted_after_subscription() -> None:
    watcher = FakeFileWatcher()
    stream = watcher.subscribe()
    # Registering the queue happens on first await; prime it so emit fans out.
    task = asyncio.ensure_future(stream.__anext__())
    await asyncio.sleep(0)  # let the generator register its queue
    assert len(watcher._subscribers) == 1

    event = _make_event()
    watcher.emit(event)
    received = await asyncio.wait_for(task, _TIMEOUT)
    assert received == event
    await stream.aclose()
    assert watcher._subscribers == []


async def test_single_emit_fans_out_to_two_concurrent_subscribers() -> None:
    watcher = FakeFileWatcher()
    stream_a = watcher.subscribe()
    stream_b = watcher.subscribe()
    task_a = asyncio.ensure_future(stream_a.__anext__())
    task_b = asyncio.ensure_future(stream_b.__anext__())
    await asyncio.sleep(0)  # let both generators register
    assert len(watcher._subscribers) == 2

    event = _make_event(kind="created", path="ROADMAP.md")
    watcher.emit(event)
    got_a = await asyncio.wait_for(task_a, _TIMEOUT)
    got_b = await asyncio.wait_for(task_b, _TIMEOUT)
    assert got_a == event
    assert got_b == event
    await stream_a.aclose()
    await stream_b.aclose()


async def test_cancelled_subscriber_is_unregistered_and_does_not_block_others() -> None:
    watcher = FakeFileWatcher()
    open_stream = watcher.subscribe()
    doomed_stream = watcher.subscribe()
    open_task = asyncio.ensure_future(open_stream.__anext__())
    doomed_task = asyncio.ensure_future(doomed_stream.__anext__())
    await asyncio.sleep(0)
    assert len(watcher._subscribers) == 2

    # Cancel the doomed subscriber's pending await: the generator's finally-block
    # must run and unregister its queue (simulating a client disconnect).
    doomed_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await doomed_task
    assert len(watcher._subscribers) == 1  # shrank back — no leak

    # The still-open subscriber keeps working.
    event = _make_event()
    watcher.emit(event)
    received = await asyncio.wait_for(open_task, _TIMEOUT)
    assert received == event
    await open_stream.aclose()
    assert watcher._subscribers == []


# --------------------------------------------------------------------------- #
# ChangeEvent — JSON round-trip, frozen, extra='forbid'
# --------------------------------------------------------------------------- #


def test_change_event_round_trips_through_json() -> None:
    event = _make_event(kind="moved", path=".factory/run-state/T39")
    rebuilt = ChangeEvent.model_validate_json(event.model_dump_json())
    assert rebuilt == event


def test_change_event_is_frozen() -> None:
    event = _make_event()
    with pytest.raises(ValidationError):
        event.kind = "deleted"


def test_change_event_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ChangeEvent(
            kind="modified",
            path="docs/ROADMAP.md",
            scope="planning",
            at=datetime(2026, 7, 24, 12, 0, 0),
            unexpected="x",
        )


def test_change_event_rejects_absolute_path_scope_and_kind_literals() -> None:
    # scope and kind are constrained Literals — an out-of-set value is rejected.
    with pytest.raises(ValidationError):
        ChangeEvent(
            kind="renamed",  # not in the allowed set
            path="docs/ROADMAP.md",
            scope="planning",
            at=datetime(2026, 7, 24, 12, 0, 0),
        )
    with pytest.raises(ValidationError):
        ChangeEvent(
            kind="modified",
            path="docs/ROADMAP.md",
            scope="secrets",  # not in the allowed set
            at=datetime(2026, 7, 24, 12, 0, 0),
        )


@pytest.mark.parametrize(
    "abs_path", ["/etc/passwd", "/home/u/project/docs/ROADMAP.md", r"C:\project\x"]
)
def test_change_event_rejects_absolute_path(abs_path: str) -> None:
    # The project-relative security invariant is enforced on the schema itself:
    # any absolute path (POSIX or Windows) is rejected so the host's filesystem
    # layout can never leak onto the SSE wire.
    with pytest.raises(ValidationError):
        ChangeEvent(
            kind="modified",
            path=abs_path,
            scope="planning",
            at=datetime(2026, 7, 24, 12, 0, 0),
        )
