"""The ``GET /api/v1/events`` live file-change Server-Sent-Events endpoint.

The backend half of the v1 live-update epic: the SPA opens an ``EventSource`` on
this route and refreshes its views when project files change. The handler is
deliberately thin — it resolves the
:class:`~factory_console.services.watcher_supervisor.WatcherSupervisor` through the
``Depends(get_watcher_supervisor)`` seam, reads the generation and the live watcher off
IT (never through a separate ``Depends``, so the two cannot resolve at different
instants — see below), and wraps
:func:`~factory_console.services.events_service.sse_event_stream` in a
``StreamingResponse``; all stream logic (the ``ready`` handshake, per-change
frames, heartbeat keepalives, the terminal ``stale`` frame, and leak-safe
subscription release on disconnect) lives in the service.

**The watcher is resolved ONCE, per CONNECTION** — a watcher is built around one
root, so a stream is bound to the project selected at the moment it was opened,
and that is the contract the SPA builds against (T126). Re-resolving mid-stream
would be worse than useless: ``ChangeEvent.path`` is PROJECT-RELATIVE, so the same
relative path names a different file after a switch. The switch protocol is
therefore to END the stream — the handler captures the supervisor's generation at
connect and hands the stream a predicate that compares it against the CURRENT one.
Without it a connection opened before a switch would sit on a stopped watcher,
heartbeating forever and never carrying another change — a live view that has
silently stopped updating.

**Generation and watcher are read in that order, from the SAME resolved supervisor,
inside the handler body — not as two independent ``Depends``.** FastAPI runs sync
dependencies in its threadpool with no ordering guarantee between them, so a watcher
resolved as its own dependency could be captured BEFORE an in-flight project switch and
the generation read AFTER it completes — the swap's two halves
(:meth:`~factory_console.services.watcher_supervisor.WatcherSupervisor.retarget_release`
then :meth:`~factory_console.services.watcher_supervisor.WatcherSupervisor.retarget_rebuild`)
are not atomic from the read side. That skew latches a connection onto the STOPPED
watcher while its captured generation is already the NEW one, so ``is_stale()`` compares
the current generation against itself forever and never fires. Reading generation first
and the watcher second, both here, means the only possible skew runs the other way — a
watcher captured just after a swap paired with a generation captured just before it — which
is merely a spurious ``stale`` frame the client reconnects from, never a silently
un-refreshing stream.

The response is ``text/event-stream`` with ``Cache-Control: no-cache`` (SSE must
never be cached) and ``X-Accel-Buffering: no`` (tell an intermediary proxy not to
buffer the long-lived stream). Loopback-only and unauthenticated, consistent with
the 127.0.0.1 trust boundary; a ``None`` watcher degrades to a heartbeat-only
stream rather than erroring, since live updates are opt-in.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from factory_console.api.deps import get_watcher_supervisor
from factory_console.services.events_service import sse_event_stream
from factory_console.services.watcher_supervisor import WatcherSupervisor

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag (mirrors ``api/v1/search.py``).
router = APIRouter(tags=["events"])


@router.get("/events")
async def events(
    request: Request,
    supervisor: WatcherSupervisor = Depends(get_watcher_supervisor),
) -> StreamingResponse:
    """Stream live :class:`ChangeEvent`s to the client as ``text/event-stream``.

    Delegates the entire stream body to
    :func:`~factory_console.services.events_service.sse_event_stream`, which yields
    the initial ``ready`` frame, an ``event: change`` frame per file change while
    the resolved ``watcher`` is present, and periodic ``: keepalive`` comments,
    releasing the watcher subscription when the client disconnects. The
    ``no-cache`` / ``X-Accel-Buffering: no`` headers keep the long-lived stream
    un-cached and un-buffered by any intermediary.

    ``generation`` is read FIRST, then ``watcher`` — both off the same ``supervisor``,
    sequentially in this body, never as two separate ``Depends`` (see the module
    docstring for why the order and the single source matter). ``watcher`` is whichever
    was live at that instant and is not re-resolved afterwards: a stream is bound to the
    project selected at connect (see the module docstring for why). What the stream
    re-reads instead is ``supervisor.generation()``, compared on each heartbeat tick
    against the value captured here: once it moves, this connection's watcher has been
    replaced, so the stream emits a terminal ``event: stale`` frame and ends rather than
    heartbeating on a stopped watcher forever. The comparison is a pure integer read with
    no I/O, so the route stays non-blocking.

    ``supervisor`` is typed non-optional because ``create_app`` ALWAYS binds one —
    a watcher-less app is a supervisor holding nothing, not an absent supervisor —
    so ``get_watcher_supervisor`` raises rather than degrading, exactly like the
    other wiring-bug dependencies. That raise is unreachable in a real app and
    changes no client-visible behaviour here.
    """
    generation = supervisor.generation()
    watcher = supervisor.current()
    return StreamingResponse(
        sse_event_stream(
            watcher,
            request,
            is_stale=lambda: supervisor.generation() != generation,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
