# [T50] SSE live updates — subscribe to /api/v1/events and refresh the current view with a subtle indicator

milestone: v1 · track: frontend · depends_on: T45, T27 · provides: a layout-level SSE subscription that refreshes the current route when project files change, with a subtle updated/disconnected indicator that degrades gracefully

## Context

v1 introduces a backend watchdog watcher exposing an SSE stream at `/api/v1/events` (T45) — a deliberate extension beyond the MVP's no-watcher, request-scoped model. This ticket adds the frontend consumer: a native `EventSource` subscription mounted once in the root layout that, on any event, calls `invalidateAll()` to re-run the current route's loaders (the same refresh the Reload button uses), plus a subtle indicator of live/updated/disconnected state. If the stream drops or the browser can't open it, the app silently falls back to static behavior (manual Reload still works).

**Contract note:** T50 treats SSE events as an UNTYPED "something changed → refresh" trigger. It does NOT parse a typed `ChangeEvent` body, so it does NOT depend on T46 or any generated type — an SSE `text/event-stream` body does not surface in OpenAPI.

## Staged approach

1. Create `frontend/src/lib/stores/live.ts`: a client-only helper that opens an `EventSource('/api/v1/events')`, exposes a Svelte readable store of connection status (`'connecting' | 'live' | 'disconnected'`) and a bump counter / last-event timestamp, reconnects with capped backoff on error, and cleans up (`close`) on stop — guarded so it no-ops during SSR/tests (`typeof EventSource` check).
2. Create `frontend/src/lib/components/LiveIndicator.svelte`: a presentational pill showing the status/updated state (a brief "Updated" flash after a bump, dimmed when disconnected).
3. Edit `frontend/src/routes/+layout.svelte` (single owner for v1): in `onMount` start the live subscription; an `$effect` watches the event bump and calls `invalidateAll()` to refresh the current view; render `<LiveIndicator />` near the `TopBar`; stop the subscription on destroy.
4. Co-located tests: mock `EventSource` to assert status transitions and that a message triggers the invalidate/bump path.

## Critical files

- `frontend/src/lib/stores/live.ts` (new — EventSource wrapper store)
- `frontend/src/lib/components/LiveIndicator.svelte` (new)
- `frontend/src/routes/+layout.svelte` (single owner — mount subscription + invalidateAll on bump)

## Interface & data

- `live.ts` opens `EventSource('/api/v1/events')` and exposes readable status + an event bump; `+layout.svelte` effect: on bump → `invalidateAll()` (from `$app/navigation`).
- Touched BY REFERENCE: the backend SSE endpoint `GET /api/v1/events` (T45, `text/event-stream`) emitting change events when watched project files change — the frontend treats ANY event as "refresh"; it does not parse a typed JSON body (SSE is not part of the OpenAPI-generated types).
- DB ops: N/A. NFR: resilience/graceful degradation (capped-backoff reconnect, silent fallback to manual Reload if `EventSource` is unavailable or the stream drops); client-only (SSR/test-guarded); same-origin, no auth (127.0.0.1); no cache.

## Verification

`pnpm check` + `pnpm lint` + `pnpm test` green (mocked-`EventSource` unit tests for status transitions + invalidate-on-message). Manual: with a backend + watcher running, edit a fixture ticket file → the current view refreshes and the indicator flashes "Updated"; kill the stream → indicator shows disconnected and the app keeps working via Reload. End-to-end assertion lives in T53.
