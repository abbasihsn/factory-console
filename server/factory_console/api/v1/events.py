"""The ``GET /api/v1/events`` live file-change Server-Sent-Events endpoint.

The backend half of the v1 live-update epic: the SPA opens an ``EventSource`` on
this route and refreshes its views when project files change. The handler is
deliberately thin — it resolves the optional
:class:`~factory_console.file_adapter.watcher.FileWatcher` through the
``Depends(get_file_watcher)`` seam and wraps
:func:`~factory_console.services.events_service.sse_event_stream` in a
``StreamingResponse``; all stream logic (the ``ready`` handshake, per-change
frames, heartbeat keepalives, and leak-safe subscription release on disconnect)
lives in the service.

The response is ``text/event-stream`` with ``Cache-Control: no-cache`` (SSE must
never be cached) and ``X-Accel-Buffering: no`` (tell an intermediary proxy not to
buffer the long-lived stream). Loopback-only and unauthenticated, consistent with
the 127.0.0.1 trust boundary; a ``None`` watcher degrades to a heartbeat-only
stream rather than erroring, since live updates are opt-in.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from factory_console.api.deps import get_file_watcher
from factory_console.file_adapter.watcher import FileWatcher
from factory_console.services.events_service import sse_event_stream

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag (mirrors ``api/v1/search.py``).
router = APIRouter(tags=["events"])


@router.get("/events")
async def events(
    request: Request,
    watcher: FileWatcher | None = Depends(get_file_watcher),
) -> StreamingResponse:
    """Stream live :class:`ChangeEvent`s to the client as ``text/event-stream``.

    Delegates the entire stream body to
    :func:`~factory_console.services.events_service.sse_event_stream`, which yields
    the initial ``ready`` frame, an ``event: change`` frame per file change while
    the injected ``watcher`` is present, and periodic ``: keepalive`` comments,
    releasing the watcher subscription when the client disconnects. The
    ``no-cache`` / ``X-Accel-Buffering: no`` headers keep the long-lived stream
    un-cached and un-buffered by any intermediary.
    """
    return StreamingResponse(
        sse_event_stream(watcher, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
