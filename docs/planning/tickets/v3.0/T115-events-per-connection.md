# [T115] /events resolves its watcher per connection and ends the stream on a selection change

milestone: v3.0 · track: backend · depends_on: T114, T45 · provides: a live stream bound to the project selected AT CONNECTION TIME, which terminates cleanly with a named `stale` frame when the selection changes, so the client reconnects onto the new project's watcher.

## Context

`get_file_watcher` now returns whatever watcher the supervisor holds when the connection is
established, so **the stream's project is resolved PER CONNECTION**. That is the contract the SPA
builds against (T126).

This ticket makes it correct even when the client does not react. A connection opened before a switch
holds a subscription on a *stopped* watcher: it would keep receiving heartbeats forever and never
another change frame — a live view that silently stops updating, which ARCHITECTURE.md names as the
precise failure the concurrency rule exists to protect this route from. So the stream captures the
supervisor's `generation` at connect and, at each heartbeat tick, ends the response when the
generation has moved.

**A correction to how the client actually behaves, which matters for the design.**
`frontend/src/lib/stores/live.ts` deliberately DROPS `EventSource`'s native auto-reconnect — its
`onerror` closes the source, sets `disconnected`, and schedules its own capped exponential backoff.
So "the browser reconnects for us" is NOT what happens: a client that merely sees the stream close
self-heals via that backoff, showing a `disconnected` flash first, and a rapid sequence of switches
would walk the delay up. That is why this ticket emits a **named `stale` frame** before closing:
T126 adds an explicit listener that treats it as a normal event — reset the attempt counter and
reconnect immediately — rather than as a failure. Ending the stream is the fallback; the frame is the
fast path.

Checking on the heartbeat tick rather than in a second racing task keeps the single-long-lived-task
invariant the module's docstring is built around: the in-flight `__anext__` is never cancelled early,
so the subscription still unregisters exactly once in the `finally`. Nothing in `_SubscriberHub`
(a file-adapter file) is touched.

## Staged approach

1. EDIT `server/factory_console/services/events_service.py`:
   - `sse_event_stream(watcher, request, *, heartbeat_interval=15.0, is_stale: Callable[[], bool] |
     None = None)`. The `None` default keeps every existing caller and test byte-identical.
   - In BOTH loops (the subscribed loop and `_heartbeat_only_stream`), on the heartbeat branch —
     before yielding the keepalive — `break` when `is_stale()` is true.
   - Emit a final `event: stale\ndata: {}\n\n` frame before breaking. Add the frame constant beside
     `_READY_FRAME` / `_KEEPALIVE_FRAME`. Docstring: a client that listens for `stale` reconnects
     immediately without backoff (T126); a client that does not still reconnects when the stream
     closes, just more slowly and via a `disconnected` state.
2. EDIT `server/factory_console/api/v1/events.py` — take
   `supervisor: WatcherSupervisor | None = Depends(get_watcher_supervisor)`, capture
   `generation = supervisor.generation()` at connect, and pass
   `is_stale=lambda: supervisor.generation() != generation`. Explain in the docstring that the
   watcher is resolved once per CONNECTION, on purpose, and that ending the stream is the switch
   protocol.
3. EDIT `tests/integration/test_api_events.py` — add: (a) a stream opened, then a retarget, closes
   within one heartbeat and its last frame is `stale` (drive it with a tiny `heartbeat_interval`);
   (b) a fresh connection after the retarget receives change frames from the NEW root; (c) the
   existing no-watcher and change-frame cases still pass unchanged.

## Critical files

- `server/factory_console/services/events_service.py` (modify)
- `server/factory_console/api/v1/events.py` (modify)
- `tests/integration/test_api_events.py` (modify)

## Interface & data

Wire (SSE frames, additive to T45's contract): unchanged `event: ready` handshake, unchanged
`event: change` with `ChangeEvent.model_dump_json()`, unchanged `: keepalive` comment, plus a new
terminal `event: stale` with `data: {}` emitted immediately before the response ends on a selection
change.

`ChangeEvent { kind, path, scope, at }` is untouched, and its `path` stays PROJECT-RELATIVE — which
is also why a switch must end the stream: the same relative path means a different file afterwards.

Signature: `sse_event_stream(watcher: FileWatcher | None, request: Request, *,
heartbeat_interval: float = 15.0, is_stale: Callable[[], bool] | None = None) -> AsyncIterator[str]`.

Contracts by reference: `FileWatcher.subscribe()` (T39); `ChangeEvent` / `ChangeScope`
(`domain/watch.py`); ARCHITECTURE.md → Cross-cutting → "SSE checked" (the route must stay
non-blocking — `is_stale` is a pure integer comparison, no I/O).

DB ops: none. NFR flags: no blocking work added to the stream; no auth change (SSE stays ungated
behind the loopback boundary); no cache (`Cache-Control: no-cache` + `X-Accel-Buffering: no`
unchanged).

## Verification

`python -m pytest tests/integration/test_api_events.py -q`, then `python -m pytest -q`. `make lint`.
Manual: `curl -N localhost:PORT/api/v1/events` in one shell, `PUT /api/v1/projects/current` in
another, and watch the stream emit `event: stale` and close.
