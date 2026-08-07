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
  says clients ignore, purely to hold the connection open and surface a drop;
- a terminal ``event: stale`` frame with an empty ``{}`` data payload, emitted
  immediately before the response ends when the optional ``is_stale`` predicate
  reports that the watcher this connection was bound to has been replaced.

**Why a named ``stale`` frame rather than just closing.** The stream's project is
resolved ONCE, per connection (see :mod:`factory_console.api.v1.events`), so a
connection opened before a project switch holds a subscription on a STOPPED
watcher: it would heartbeat forever and never carry another change — a live view
that silently stops updating. Ending the response is therefore mandatory, and it
is the fallback every client already gets: ``frontend/src/lib/stores/live.ts``
drops ``EventSource``'s native reconnect in favour of its own capped backoff, so
a client that merely sees the stream close still self-heals, just via a
``disconnected`` flash and a delay that walks up across rapid switches. The named
frame is the fast path: a client that listens for ``stale`` (T126) treats it as a
normal event — reset the attempt counter and reconnect immediately onto the new
project's watcher.

The staleness check runs on the HEARTBEAT TICK rather than in a second racing
task, which is what keeps the single-long-lived-task invariant below intact.

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
from collections.abc import AsyncIterator, Callable

from starlette.requests import Request

from factory_console.domain.watch import ChangeEvent
from factory_console.file_adapter.watcher import FileWatcher

# The initial handshake frame and the per-heartbeat comment are constants so the
# exact bytes on the wire live in one place and the tests pin them verbatim.
_READY_FRAME = "event: ready\ndata: {}\n\n"
_KEEPALIVE_FRAME = ": keepalive\n\n"
_STALE_FRAME = "event: stale\ndata: {}\n\n"


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
    is_stale: Callable[[], bool] | None = None,
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

    ``is_stale`` is the optional "is my watcher still the current one?" predicate
    the route builds from the
    :class:`~factory_console.services.watcher_supervisor.WatcherSupervisor`'s
    generation. Both paths consult it on the same heartbeat tick, just before the
    keepalive, and when it reports ``True`` they yield a terminal ``event: stale``
    frame and END the stream — because a connection whose watcher was replaced can
    never carry another change. It must stay a cheap, non-blocking check (a plain
    integer comparison); it is called from the stream body, on the event loop.

    A client that listens for ``stale`` (T126) reconnects IMMEDIATELY, without
    backoff, onto the newly selected project's watcher; a client that does not
    still reconnects when the closed stream trips its own ``disconnected`` state
    and capped backoff — slower, but never stuck. Leaving ``is_stale`` ``None``
    (the default) disables the check entirely, which is the pre-v3.0 behaviour
    every other caller keeps.

    Checking here rather than in a second racing task is what preserves the
    single-long-lived-task invariant above: the in-flight ``__anext__`` is never
    cancelled early, and the subscription is still released exactly once in the
    one ``finally``.
    """
    yield _READY_FRAME

    if watcher is None:
        async for frame in _heartbeat_only_stream(request, heartbeat_interval, is_stale):
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
            elif is_stale is not None and is_stale():
                yield _STALE_FRAME
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
    is_stale: Callable[[], bool] | None = None,
) -> AsyncIterator[str]:
    """Yield ``: keepalive`` comments every ``heartbeat_interval`` until disconnect.

    The ``watcher is None`` degradation: no subscription, just a bounded sleep
    between heartbeats with a disconnect check, so the connection is held open and
    a dropped client is noticed without ever touching a watcher.

    ``is_stale`` is consulted on the same tick, after the disconnect check and
    before the keepalive, ending the stream with a terminal ``event: stale`` frame
    exactly as the subscribed loop does. A watcher-less connection ends on a swap
    too, and deliberately: the selection it was opened under is gone, and the swap
    may well have produced the watcher this client should be subscribed to.
    """
    while True:
        await asyncio.sleep(heartbeat_interval)
        if await request.is_disconnected():
            break
        if is_stale is not None and is_stale():
            yield _STALE_FRAME
            break
        yield _KEEPALIVE_FRAME
