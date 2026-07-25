"""Server-Sent-Events stream builder for the ``GET /api/v1/events`` endpoint.

Holds the async-generator body the SSE route wraps in a ``StreamingResponse`` so
the HTTP handler stays thin: :func:`sse_event_stream` fans one long-lived
:class:`~factory_console.file_adapter.watcher.FileWatcher` out to the requesting
browser, translating each :class:`~factory_console.domain.watch.ChangeEvent` into
a wire frame and interleaving heartbeat comments so a dropped connection is
detected rather than leaking a dangling subscription.

Wire format (a well-formed SSE stream — see the WHATWG ``EventSource`` spec):
- an initial ``event: ready`` frame with an empty ``{}`` data payload, so the
  client knows the stream is live before any file changes;
- per change, an ``event: change`` frame whose ``data:`` line is
  ``ChangeEvent.model_dump_json()``. The model's fields are all single lowercase
  words (``kind``/``path``/``scope``/``at``), so that JSON is ALREADY camelCase —
  no alias generator is involved, the wire shape is the field names verbatim;
- a ``: keepalive`` comment every ``heartbeat_interval`` seconds, which the spec
  says clients ignore, purely to hold the connection open and surface a drop.

Subscription leak-safety is the subtle part. ``watcher.subscribe()`` is the hub's
async generator, which registers its queue and removes it in a ``finally``. A
heartbeat must NOT cancel the in-flight ``__anext__`` (that would throw
``CancelledError`` into the generator, run its ``finally``, and silently
unregister the subscriber after the first heartbeat). So a SINGLE long-lived task
awaits the next event ACROSS heartbeats, and the subscription is closed exactly
once in the outer ``finally`` — on client disconnect, cancellation, or normal
exit — leaving no dangling queue on the hub.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from starlette.requests import Request

from factory_console.domain.watch import ChangeEvent
from factory_console.file_adapter.watcher import FileWatcher

# The initial handshake frame and the per-heartbeat comment are constants so the
# exact bytes on the wire live in one place and the tests pin them verbatim.
_READY_FRAME = "event: ready\ndata: {}\n\n"
_KEEPALIVE_FRAME = ": keepalive\n\n"


def _change_frame(event: ChangeEvent) -> str:
    """Format one :class:`ChangeEvent` as an ``event: change`` SSE data frame.

    The ``data:`` payload is ``event.model_dump_json()`` — already camelCase
    because every field name is a single lowercase word — terminated by the blank
    line that closes an SSE frame.
    """
    return f"event: change\ndata: {event.model_dump_json()}\n\n"


async def sse_event_stream(
    watcher: FileWatcher | None,
    request: Request,
    *,
    heartbeat_interval: float = 15.0,
) -> AsyncIterator[str]:
    """Yield the SSE frames for one client connection to ``GET /api/v1/events``.

    Emits the ``ready`` handshake frame first, then:

    - when ``watcher`` is not ``None``, subscribes via ``watcher.subscribe()`` and
      yields a ``change`` frame per :class:`ChangeEvent`, interleaving a
      ``: keepalive`` comment whenever ``heartbeat_interval`` seconds elapse with
      no event. A single long-lived task awaits the next event across heartbeats
      so a heartbeat timeout never cancels the subscriber's in-flight
      ``__anext__`` (which would run the hub's ``finally`` and unregister the
      queue early); the subscription is closed exactly once in the ``finally``, so
      a client disconnect or cancellation releases it with no leak;
    - when ``watcher`` is ``None``, degrades to ``ready`` plus ``: keepalive``
      comments only, sleeping ``heartbeat_interval`` between them and never
      subscribing.

    Both paths poll ``request.is_disconnected()`` at each heartbeat and stop when
    the client goes away, so an idle connection cannot pin resources forever.
    """
    yield _READY_FRAME

    if watcher is None:
        async for frame in _heartbeat_only_stream(request, heartbeat_interval):
            yield frame
        return

    sub = watcher.subscribe()
    next_task: asyncio.Future[ChangeEvent] = asyncio.ensure_future(sub.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({next_task}, timeout=heartbeat_interval)
            if next_task in done:
                try:
                    event = next_task.result()
                except StopAsyncIteration:
                    break
                yield _change_frame(event)
                next_task = asyncio.ensure_future(sub.__anext__())
            elif await request.is_disconnected():
                break
            else:
                yield _KEEPALIVE_FRAME
    finally:
        next_task.cancel()
        with contextlib.suppress(BaseException):
            await next_task
        await sub.aclose()


async def _heartbeat_only_stream(
    request: Request,
    heartbeat_interval: float,
) -> AsyncIterator[str]:
    """Yield ``: keepalive`` comments every ``heartbeat_interval`` until disconnect.

    The ``watcher is None`` degradation: no subscription, just a bounded sleep
    between heartbeats with a disconnect check, so the connection is held open and
    a dropped client is noticed without ever touching a watcher.
    """
    while True:
        await asyncio.sleep(heartbeat_interval)
        if await request.is_disconnected():
            break
        yield _KEEPALIVE_FRAME
