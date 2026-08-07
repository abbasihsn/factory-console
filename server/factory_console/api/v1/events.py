"""The ``GET /api/v1/events`` live file-change Server-Sent-Events endpoint.

The backend half of the v1 live-update epic: the SPA opens an ``EventSource`` on
this route and refreshes its views when project files change. The handler is
deliberately thin — it resolves the optional
:class:`~factory_console.file_adapter.watcher.FileWatcher` through the
``Depends(get_file_watcher)`` seam and wraps
:func:`~factory_console.services.events_service.sse_event_stream` in a
``StreamingResponse``; all stream logic (the ``ready`` handshake, per-change
frames, heartbeat keepalives, the terminal ``stale`` frame, and leak-safe
subscription release on disconnect) lives in the service.

**The watcher is resolved ONCE, per CONNECTION** — a watcher is built around one
root, so a stream is bound to the project selected at the moment it was opened,
and that is the contract the SPA builds against (T126). Re-resolving mid-stream
would be worse than useless: ``ChangeEvent.path`` is PROJECT-RELATIVE, so the same
relative path names a different file after a switch. The switch protocol is
therefore to END the stream — the handler also takes the
:class:`~factory_console.services.watcher_supervisor.WatcherSupervisor` through
``Depends(get_watcher_supervisor)``, captures its generation at connect, and hands
the stream a predicate that compares the two. Without it a connection opened
before a switch would sit on a stopped watcher, heartbeating forever and never
carrying another change — a live view that has silently stopped updating.

The response is ``text/event-stream`` with ``Cache-Control: no-cache`` (SSE must
never be cached) and ``X-Accel-Buffering: no`` (tell an intermediary proxy not to
buffer the long-lived stream). Loopback-only and unauthenticated, consistent with
the 127.0.0.1 trust boundary; a ``None`` watcher degrades to a heartbeat-only
stream rather than erroring, since live updates are opt-in.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from factory_console.api.deps import get_file_watcher, get_watcher_supervisor
from factory_console.file_adapter.watcher import FileWatcher
from factory_console.services.events_service import sse_event_stream
from factory_console.services.watcher_supervisor import WatcherSupervisor

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag (mirrors ``api/v1/search.py``).
router = APIRouter(tags=["events"])


@router.get("/events")
async def events(
    request: Request,
    watcher: FileWatcher | None = Depends(get_file_watcher),
    supervisor: WatcherSupervisor = Depends(get_watcher_supervisor),
) -> StreamingResponse:
    """Stream live :class:`ChangeEvent`s to the client as ``text/event-stream``.

    Delegates the entire stream body to
    :func:`~factory_console.services.events_service.sse_event_stream`, which yields
    the initial ``ready`` frame, an ``event: change`` frame per file change while
    the injected ``watcher`` is present, and periodic ``: keepalive`` comments,
    releasing the watcher subscription when the client disconnects. The
    ``no-cache`` / ``X-Accel-Buffering: no`` headers keep the long-lived stream
    un-cached and un-buffered by any intermediary.

    ``watcher`` is whichever watcher was live when THIS connection was established
    and is not re-resolved afterwards (see the module docstring for why). What the
    stream re-reads instead is ``supervisor.generation()``, captured here at connect
    and compared on each heartbeat tick: once it moves, this connection's watcher
    has been replaced, so the stream emits a terminal ``event: stale`` frame and
    ends rather than heartbeating on a stopped watcher forever. The comparison is a
    pure integer read with no I/O, so the route stays non-blocking.

    ``supervisor`` is typed non-optional because ``create_app`` ALWAYS binds one —
    a watcher-less app is a supervisor holding nothing, not an absent supervisor —
    so ``get_watcher_supervisor`` raises rather than degrading, exactly like the
    other wiring-bug dependencies. That raise is unreachable in a real app and
    changes no client-visible behaviour here.
    """
    generation = supervisor.generation()
    return StreamingResponse(
        sse_event_stream(
            watcher,
            request,
            is_stale=lambda: supervisor.generation() != generation,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
