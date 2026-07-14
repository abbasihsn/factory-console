# [T30] Ticket list route `/` with server-side filter + search

milestone: MVP · track: frontend · depends_on: T28, T29, T22 · provides: Root route rendering ticket list with filters (status/track/milestone) + substring search; all filtering server-side

## Context

First user-visible screen. Overview list showing id, title, badges, track, milestone, dep counts. Filtering + search hit the backend (`?status=&track=&milestone=&q=`) — never client-side — so 100 tickets scale trivially and behavior matches whatever backend/file-adapter decide about matching semantics. Empty + error states covered.

## Staged approach

1. `src/routes/+page.ts` (load) reads URL query params, calls `listTickets({ status, track, milestone, q })`, returns `{ items, total, filters }`; on error re-throws so `+error.svelte` handles it.
2. `src/lib/components/TicketRow.svelte`: id (monospace link to `/tickets/{id}`), title, `StatusBadge`, `RunStateBadge`, track chip, milestone chip, `depCount → dependentCount`.
3. `src/lib/components/FiltersBar.svelte`: three `<select>` (status/track/milestone with 'All' entries; dynamised in v1) + debounced (250ms) `<input>` for `q`. On change `goto('?' + new URLSearchParams(...))` with `keepFocus/noScroll` so SvelteKit re-runs `+page.ts`.
4. `src/routes/+page.svelte` renders `FiltersBar` + list of `TicketRow`. Empty state: "No tickets match — clear filters?" with reset link.
5. Vitest: `+page.svelte` renders fixture items with correct row count; empty state; `FiltersBar` URL update; `TicketRow` snapshot.

## Critical files

- `frontend/src/routes/+page.ts`
- `frontend/src/routes/+page.svelte`
- `frontend/src/routes/+page.test.ts`
- `frontend/src/lib/components/TicketRow.svelte`
- `frontend/src/lib/components/TicketRow.test.ts`
- `frontend/src/lib/components/FiltersBar.svelte`
- `frontend/src/lib/components/FiltersBar.test.ts`

## Interface & data

Consumes `GET /api/v1/tickets` with `?status=&track=&milestone=&q=` -> `{ items: TicketSummary[], total: number }`. Server-side filtering enforced (do NOT filter client-side); debounce 250ms; URL is source of truth for filter state.

## Verification

`pnpm dev` against backend + `with_run_state` fixture: visiting `/` lists all fixture tickets; typing in search updates URL + re-fetches; filter narrows; clearing filters restores; empty state on no-match. `pnpm test` passes.
