# [T126] The live stream follows the selection

milestone: v3.0 · track: frontend · depends_on: T122, T125, T115, T50, T45 · provides: the SSE subscription and its indicator are re-established on a project switch — immediately and without a backoff penalty — so `LiveIndicator` never claims "Live" about a stream that belongs to the previously-selected project.

## Context

`stores/live.ts` opens ONE `EventSource` to `/api/v1/events` for the life of the page and treats every
frame as an untyped "something changed → `invalidateAll()`". That was sound when the project was fixed
at boot. With a switchable project the stream's meaning changes underneath it: a connection opened
while project A was selected is, at best, ambiguous about project B, and the indicator showing a
confident "Live" over it is precisely the "a stalled or mis-scoped loop shows up as a live view that
silently stops updating" failure the SSE house rule flags.

**Two facts about the existing client shape this ticket, and both are easy to get wrong.**

First, `live.ts` deliberately DROPS `EventSource`'s native auto-reconnect: its `onerror` closes the
source, sets `disconnected`, and schedules its own capped exponential backoff. So merely letting the
server close the stream (T115) is not enough — the user would see a `disconnected` flash, and a rapid
sequence of switches would walk the delay up. This ticket adds an explicit **`stale` listener** that
treats the server's terminal frame as a normal event: reset the attempt counter and reconnect
immediately.

Second, the client listens only for `change` and `onmessage`, and per the `EventSource` spec a named
frame does NOT trigger `onmessage` — so without an explicit `addEventListener('stale', …)` the frame
T115 sends would be silently discarded.

And the restart trigger must key on the selected project **ID VALUE**, not on the layout data object:
`+layout.svelte` already calls `invalidateAll()` on every SSE bump, which re-runs the layout load and
yields a fresh object each time, so keying on identity would restart the stream on every file change.

## Staged approach

1. `src/lib/stores/live.ts`:
   - Add `restart(): void` to the `LiveStore` interface — `stop()` then `start()`, **resetting the
     backoff attempt counter to 0** so a switch does not inherit a long pending delay, and a
     no-op-safe path when `EventSource` is unavailable (the existing graceful degradation).
   - Add an explicit `es.addEventListener('stale', …)` handler that resets `attempt` and reconnects
     immediately, without passing through the `disconnected` state — a selection change is a normal
     event, not a failure.
   - Keep every other behaviour byte-identical; these are additive, not a rework.
2. `src/routes/+layout.svelte`: key an `$effect` on the selected project **id value** from the layout
   data and call `live.restart()` when it CHANGES (skipping the first run, exactly as the existing
   `$bump > 0` guard skips the initial bump). Keying on the layout data rather than on the switcher's
   own callback is deliberate: it also fires when the selection was changed in ANOTHER tab and this
   tab learned about it on a re-load.
3. Add a comment in the layout stating the contract this relies on — the server resolves the stream's
   project per CONNECTION (T115) — and cross-reference that ticket.
4. `src/lib/stores/live.test.ts`: `restart()` closes the previous `EventSource` and constructs a new
   one (the injectable `eventSourceCtor` already makes this observable), resets the backoff, and is
   safe with no `EventSource`; a `stale` frame triggers an immediate reconnect with no `disconnected`
   transition; **two switches in quick succession do not walk the backoff**.

## Critical files

- `frontend/src/lib/stores/live.ts` (modify)
- `frontend/src/routes/+layout.svelte` (modify — aggregation file)
- `frontend/src/lib/stores/live.test.ts` (modify)

## Interface & data

`LiveStore` gains `restart(): void`; no change to `status` / `bump` / `lastEvent`.

Contracts by reference: REST v1 `GET /api/v1/events` (SSE — named `change` frames, the `ready`
handshake, and T115's new terminal `event: stale`) and the Cross-cutting SSE note in
ARCHITECTURE.md.

DB ops: none. NFR flags: **no retry-storm** — a switch resets rather than adds to the capped
exponential backoff, and the `stale` path bypasses backoff entirely; the stream stays a pure refresh
trigger and its body is still never parsed.

Aggregation note: `routes/+layout.svelte` is shared with T122 and T125, both dependencies of this
ticket.

## Verification

`pnpm --dir frontend test`, `pnpm --dir frontend check`, `pnpm --dir frontend lint`, `make lint`.
Manual via `./scripts/dev.sh` with two registered projects: watch the network panel — a switch closes
the old `/api/v1/events` connection and opens a new one with no `disconnected` flash, and
`LiveIndicator` stays on "Live"; then touch a watched file in the newly-selected project and confirm
the view refreshes.
