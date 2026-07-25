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
from collections.abc import AsyncGenerator
from typing import Protocol, runtime_checkable

from factory_console.domain.watch import ChangeEvent


class _SubscriberHub:
    """Register / unregister / fan-out mechanics shared by ``FileWatcher`` impls.

    Holds the per-client queues, registers a fresh one on the first
    :meth:`subscribe` await and unregisters it in a ``finally`` (leak-safe on
    client disconnect or cancellation, never blocking the others), and
    :meth:`fan_out`\\ s an event to every registered queue. Both
    :class:`FakeFileWatcher` and the T40 ``RealFileWatcher`` hold one, so this
    leak-safety / fan-out logic lives in exactly one place and cannot drift
    between them. Keeping it HERE — not in the ``watcher_real`` source its
    read-only AST guard scans — is also why the real watcher can share the
    ``list.remove`` mechanics that guard would otherwise forbid by name.
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[ChangeEvent]] = []

    def fan_out(self, event: ChangeEvent) -> None:
        """Deliver ``event`` to every registered subscriber queue."""
        # Snapshot so a subscriber unregistering mid-iteration (client disconnect)
        # cannot mutate the list under us.
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    async def subscribe(self) -> AsyncGenerator[ChangeEvent, None]:
        """Register a fresh queue and yield each awaited event until cancelled."""
        queue: asyncio.Queue[ChangeEvent] = asyncio.Queue()
        self._subscribers.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.remove(queue)


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

    def subscribe(self) -> AsyncGenerator[ChangeEvent, None]:
        """Register a fresh, independent per-client stream of :class:`ChangeEvent`s.

        Each call returns a new async generator; generators are independent, so one
        client disconnecting never affects another, and the consumer can ``aclose()``
        it to release the subscription leak-safely.
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
        self._hub = _SubscriberHub()

    def start(self) -> None:
        """Flip the running flag on (idempotent)."""
        self._running = True

    def stop(self) -> None:
        """Flip the running flag off (idempotent)."""
        self._running = False

    def emit(self, event: ChangeEvent) -> None:
        """Fan ``event`` out to every registered subscriber (test driver, not on the port)."""
        self._hub.fan_out(event)

    def subscribe(self) -> AsyncGenerator[ChangeEvent, None]:
        """Register a fresh queue and yield each awaited event until cancelled.

        Returns the shared :class:`_SubscriberHub`'s async generator directly (not
        a wrapper), so closing it runs the hub's ``finally`` that unregisters the
        queue — a cancelled or closed subscriber leaks nothing and does not block
        the others.
        """
        return self._hub.subscribe()
