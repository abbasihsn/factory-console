# [T45] GET /api/v1/events SSE endpoint + EventsService (live file-change stream)

milestone: v1 · track: backend · depends_on: T44, T39 · provides: GET /api/v1/events streaming file-change notifications as text/event-stream for live SPA updates

## Context

Completes the v1 live-update epic: the frontend opens an `EventSource` to `/api/v1/events` and refreshes views when project files change. This endpoint bridges the `FileWatcher` (wired by T44) to an SSE stream, fanning one watcher out to N browser subscribers in the single process. Loopback-only, no auth, consistent with the 127.0.0.1 trust boundary.

## Staged approach

1. Create `server/factory_console/services/events_service.py`: an async generator `sse_event_stream(watcher, request) -> AsyncIterator[str]` that yields an initial `event: ready` frame, subscribes to the watcher (`watcher.subscribe()`), formats each `ChangeEvent` as `event: change\ndata: <camelCase JSON>\n\n` (`model_dump_json`), interleaves a `: keepalive\n\n` heartbeat (~15s) to hold the connection and detect drops, stops when the client disconnects (`await request.is_disconnected()` / generator cancellation) and always releases the subscription; when `watcher` is `None` it degrades to ready + heartbeats only.
2. Create `server/factory_console/api/v1/events.py`: a tags-only `APIRouter` with `async def events(request, watcher=Depends(get_file_watcher))` returning `StreamingResponse(sse_event_stream(watcher, request), media_type='text/event-stream', headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})` (starlette `StreamingResponse` — no new dependency).
3. Append `router.include_router(events_router)` + its import to `api/v1/__init__.py` (shared router registry — declared in critical_files so it serializes against T41/T42).

## Critical files

- `server/factory_console/services/events_service.py` (new)
- `server/factory_console/api/v1/events.py` (new)
- `server/factory_console/api/v1/__init__.py` (register the sub-router)

## Interface & data

- Request: `GET /api/v1/events`. Response: 200 `text/event-stream`; frames: initial `event: ready`, then per change `event: change\ndata: {<ChangeEvent as camelCase JSON>}\n\n`, plus periodic `: keepalive` comments.
- `ChangeEvent` is the T39 model (referenced, not redefined) — the SSE data payload is documented by reference since a stream is not captured as a JSON model by `openapi-typescript`.
- Consumes: `get_file_watcher` (`FileWatcher.subscribe` from T44). No DB. NFR: SSE long-lived connection; loopback-only, no auth/CORS/CSRF; heartbeat keepalive; client-disconnect cancels the subscription (no leak); single watcher fans out to many subscribers; `watcher=None` degrades to heartbeats-only.

## Verification

`pytest` with `httpx.AsyncClient` streaming over `create_app(FakeFileAdapter, file_watcher=<fake FileWatcher that can be fed events>)`: open `GET /api/v1/events`, assert the response is `text/event-stream` and the initial ready frame arrives; push a fake `ChangeEvent` (via the fake's `emit`) and assert a well-formed `event: change` data frame with camelCase fields is received, then disconnect and assert the subscription was released. Test `watcher=None` path yields ready + a heartbeat and no crash. (Frontend E2E for watcher-triggered live update is T53.)
