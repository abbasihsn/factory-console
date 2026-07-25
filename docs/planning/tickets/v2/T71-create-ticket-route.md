# [T71] Create-ticket route + 'New ticket' affordance on the list

milestone: v2 · track: frontend · depends_on: T30, T65, T66, T67, T68, T69 · provides: /tickets/new route reusing TicketForm with dry-run preview + save+confirm create, plus a 'New ticket' button on the ticket list.

## Context

Completes v2 by letting users author a new ticket. A dedicated `/tickets/new` route (a shareable URL, cleaner than list-modal state) reuses `TicketForm` in create mode; on submit it requests a dry-run create preview, shows `DiffPreviewModal`, and on save+confirm calls `createTicket` with the session token and navigates to the new ticket. The list gains a 'New ticket' affordance. Reuses the same token prompt and error surface as the edit flow, so no new plumbing.

## Staged approach

1. Add `src/routes/tickets/new/+page.svelte`: render `TicketForm(mode='create', empty initial)`; on submit call `previewWrite` (dry-run create) and open `DiffPreviewModal`; on confirm read the token (or show `WriteTokenPrompt`) and call `createTicket(values, token)`; on success `goto('/tickets/{id}')`; surface `ApiError` via `ApiErrorView`.
2. Add `src/routes/tickets/new/+page.ts` returning the empty initial form values (no data load; SSR/prerender already disabled at the root layout).
3. Modify `src/routes/+page.svelte`: add a 'New ticket' button in the list header linking to `/tickets/new` (presentational only; keep the existing filter/list logic untouched).

## Critical files

- `frontend/src/routes/tickets/new/+page.svelte` (new)
- `frontend/src/routes/tickets/new/+page.ts` (new)
- `frontend/src/routes/+page.svelte`

## Interface & data

Flow: `TicketForm(create)` values → `previewWrite` (dry-run create) → `DiffPreviewModal` → `createTicket(TicketCreate, token)` → `goto('/tickets/{id}')`. `+page.ts` returns default empty `TicketFormValues`. By reference: POST /api/v1/tickets + dry-run endpoint and write-token header (T65/T66); `TicketCreate` schema (regenerated types); `TICKET_ID_PATTERN` required-id validation via `TicketForm`/T67; `ApiError` via `ApiErrorView`. No DB. NFR: AUTH (token, prompt when missing); create gated behind dry-run + save+confirm; `id` uniqueness/validity is server-enforced and surfaced as an `ApiError` (the 400 `invalid_ticket_id` / 409 `write_conflict` code path). Note: `routes/+page.svelte` is edited only by this v2 ticket — no aggregation-file collision.

## Verification

Vitest `routes/tickets/new/page.test.ts` (mocked `$lib/api`: valid form → previewWrite → confirm → createTicket → navigate; duplicate/invalid id from the server renders ApiErrorView; missing token shows prompt) and a list `page.test.ts` addition asserting the 'New ticket' link targets `/tickets/new`. The T74/T75 Playwright e2e covers the full path. `pnpm check`, `pnpm test`, `pnpm lint`, `pnpm build` green.
