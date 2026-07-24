"""The long-lived :class:`FileWatcher` port + a deterministic fake.

This module is the ONE deliberate break from the MVP's "no database, no cache,
NO watcher" rule (see ``ARCHITECTURE.md`` "## One-line" + "## Cross-cutting"): a
long-lived component now runs alongside the otherwise stateless per-request
:class:`~factory_console.file_adapter.protocol.FileAdapter`. To keep that
stateless-adapter invariant intact, the watcher is a SEPARATE port — NOT a
method on the per-request ``FileAdapter``. It exists so the backend SSE endpoint
(``/api/v1/events``, T45) can be built and tested before any watchdog wiring
exists.

Two implementations satisfy the port structurally: the deterministic
:class:`FakeFileWatcher` below (no threads, no clock, no filesystem — tests
``emit()`` an event then assert receipt) and the watchdog-backed
``RealFileWatcher`` that lands in T40. Consumers and tests import both symbols by
full path (``from factory_console.file_adapter.watcher import FileWatcher,
FakeFileWatcher``); this module is deliberately NOT re-exported from
``file_adapter/__init__``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from factory_console.domain.watch import ChangeEvent


@runtime_checkable
class FileWatcher(Protocol):
    """Long-lived port that streams :class:`ChangeEvent`s to per-client subscribers.

    ``@runtime_checkable`` lets tests assert an implementation satisfies the port
    with ``isinstance`` — a structural check on method presence only, not on
    signatures. The port is opt-in: it is constructed and ``start()``-ed only
    when the backend enables live updates, and it is read-only — a watcher never
    mutates the project.
    """

    def start(self) -> None:
        """Begin watching (idempotent — calling twice is safe)."""
        ...

    def stop(self) -> None:
        """Halt watching and release resources (idempotent — calling twice is safe)."""
        ...

    def subscribe(self) -> AsyncIterator[ChangeEvent]:
        """Register a fresh, independent per-client stream of :class:`ChangeEvent`s.

        Each call returns a new async iterator; iterators are independent, so one
        client disconnecting never affects another.
        """
        ...


class FakeFileWatcher:
    """Deterministic, test-drivable :class:`FileWatcher` — no threads/clock/FS.

    Keeps a list of :class:`asyncio.Queue` subscribers. ``start()``/``stop()``
    flip a running flag (both idempotent). The test-only :meth:`emit` (NOT on the
    ``FileWatcher`` Protocol) fans an event out to every registered subscriber,
    and :meth:`subscribe` yields awaited events until the consumer stops
    iterating, unregistering its queue in a ``finally`` so client-disconnect or
    cancellation is leak-safe and never blocks the other subscribers.
    """

    def __init__(self) -> None:
        self._running = False
        self._subscribers: list[asyncio.Queue[ChangeEvent]] = []

    def start(self) -> None:
        """Flip the running flag on (idempotent)."""
        self._running = True

    def stop(self) -> None:
        """Flip the running flag off (idempotent)."""
        self._running = False

    def emit(self, event: ChangeEvent) -> None:
        """Fan ``event`` out to every registered subscriber (test driver, not on the port)."""
        for queue in self._subscribers:
            queue.put_nowait(event)

    async def subscribe(self) -> AsyncIterator[ChangeEvent]:
        """Register a fresh queue and yield each awaited event until cancelled.

        The queue is unregistered in a ``finally`` block, so a cancelled or
        closed subscriber leaks nothing and does not block the others.
        """
        queue: asyncio.Queue[ChangeEvent] = asyncio.Queue()
        self._subscribers.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.remove(queue)
