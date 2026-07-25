# [T70] Wire gated edit + delete into the ticket detail route

milestone: v2 · track: frontend · depends_on: T29, T31, T65, T66, T67, T68, T69 · provides: Edit/Delete affordances on /tickets/[id] gated by run-state + EditGate banner + the full edit→dry-run→save+confirm and delete→confirm flows wired to the write endpoints.

## Context

This is where editing becomes usable on the detail page. For a ticket whose `runState` is not `todo`/`unknown`, edit/delete are disabled and an `EditGate` banner explains why (a UI mirror of the server `RunStateGate`). For an editable ticket, Edit opens `TicketForm`, requests a dry-run preview, shows `DiffPreviewModal`, and on save+confirm calls `updateTicket` with the session token; Delete opens `ConfirmDialog` and calls `deleteTicket` then returns to the list. A missing token surfaces the `WriteTokenPrompt` first. Failures render via the existing `ApiErrorView`. An `EditTicketModal` orchestrator keeps the change to `+page.svelte` minimal.

## Staged approach

1. Add `src/lib/components/EditGate.svelte`: props `{ runState: RunState }`; when `!isEditable(runState)` render an explanatory read-only banner (in-flight/ready/merged are immutable, matching the server gate).
2. Add `src/lib/components/EditTicketModal.svelte`: props `{ ticket: Ticket; open: boolean; onClose: () => void; onSaved: () => void }`; host `TicketForm(mode='edit', initial from ticket)`; on submit call `previewWrite` (dry-run update) and open `DiffPreviewModal`; on confirm read the token from the `writeToken` store (or show `WriteTokenPrompt` if absent) and call `updateTicket(id, values, token)`; on success call `onSaved`; surface any `ApiError` via `ApiErrorView`.
3. Modify `src/routes/tickets/[id]/+page.svelte`: render `<EditGate runState={ticket.runState} />`; add Edit + Delete buttons enabled only when `isEditable(ticket.runState)`; mount `<EditTicketModal>` and a delete `<ConfirmDialog>` that calls `deleteTicket` (with token) then `goto('/')`; after a successful edit, `invalidateAll()`/refresh so the detail reflects the write. Keep the `+page.svelte` delta small by delegating orchestration to `EditTicketModal`.

## Critical files

- `frontend/src/lib/components/EditGate.svelte` (new)
- `frontend/src/lib/components/EditTicketModal.svelte` (new)
- `frontend/src/routes/tickets/[id]/+page.svelte`

## Interface & data

`EditGate` props `{ runState }`; `EditTicketModal` props `{ ticket: Ticket; open; onClose; onSaved }`. Flow: `TicketForm` values → `previewWrite` (dry-run) → `DiffPreviewModal` → `updateTicket(id, TicketUpdate, token)`; delete → `ConfirmDialog` → `deleteTicket(id, token)` → `goto('/')`. By reference: PUT/DELETE + dry-run endpoints and the write-token header (T65/T66); `RunState` editing gate mirrored via `isEditable` (T67); `ApiError` envelope via `ApiErrorView`. No DB (server owns tmp-write+rename). NFR: AUTH (token required, prompt when missing); non-todo gating enforced client-side as a mirror, never the sole gate; destructive delete behind confirm; refresh/invalidate after write.

## Verification

Vitest `EditGate.test.ts` (banner shown only for non-editable states) and `EditTicketModal.test.ts` (mocked `$lib/api`: submit → previewWrite → confirm → updateTicket with token; missing token shows prompt; error renders ApiErrorView). Detail `page.test.ts` additions: Edit/Delete disabled for in-flight/ready/merged, enabled + gate hidden for todo. The Playwright create/edit/save e2e (T74/T75) exercises the end-to-end path. `pnpm check`, `pnpm test`, `pnpm lint` green.
