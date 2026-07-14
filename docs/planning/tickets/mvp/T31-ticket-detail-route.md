# [T31] Ticket detail route `/tickets/[id]`

milestone: MVP · track: frontend · depends_on: T30, T29, T22 · provides: Detail route rendering full ticket body, badges, chips, deps/provides, file paths, link to dep-neighborhood

## Context

Primary drill-down view. Renders every field of `Ticket`: title, `StatusBadge`, `RunStateBadge`, track + milestone chips, `dependsOn` chips (each a link), `provides` chips, `files` list (plain text — no filesystem access from SPA), `MarkdownBody` for `bodyHtml`. Prominent link to `/tickets/[id]/deps`. 404 renders friendly not-found panel (distinct from generic error page).

## Staged approach

1. `src/routes/tickets/[id]/+page.ts` calls `getTicket(params.id)`; on `ApiError` with `status===404` return `{ notFound: true, id: params.id }` instead of throwing; other errors re-throw.
2. `src/routes/tickets/[id]/+page.svelte`: if `notFound` render centered "Ticket \"{id}\" not found — [back to list]" panel. Otherwise: header (title + `StatusBadge` + `RunStateBadge`); chip row for track + milestone; `dependsOn` chips where each chip is `<a href="/tickets/{depId}">{depId}</a>`; `provides` chips (plain); `files` block (monospace `<ul>`); link "View dep neighborhood →" to `/tickets/[id]/deps`; `<MarkdownBody html={ticket.bodyHtml} />`.
3. `src/lib/components/ChipList.svelte` (props `{ items: {label, href?}[] }`) to keep chip rendering consistent between deps/provides.
4. Vitest: renders fixture ticket with all fields; not-found panel renders; dep chips are anchor tags with correct hrefs; `MarkdownBody` receives `bodyHtml` verbatim.

## Critical files

- `frontend/src/routes/tickets/[id]/+page.ts`
- `frontend/src/routes/tickets/[id]/+page.svelte`
- `frontend/src/routes/tickets/[id]/+page.test.ts`
- `frontend/src/lib/components/ChipList.svelte`
- `frontend/src/lib/components/ChipList.test.ts`

## Interface & data

Consumes `GET /api/v1/tickets/{id} -> Ticket`. `TICKET_ID_PATTERN` validated server-side; SPA passes through. `bodyHtml` server-sanitized so `{@html}` in `MarkdownBody` is safe. File paths rendered as plain text NEVER as `file://` links.

## Verification

`pnpm dev` against `with_run_state` fixture: `/tickets/<known-id>` renders full ticket; `dependsOn` chips are working links; `/tickets/does-not-exist` renders not-found panel; `pnpm test` passes; `pnpm build && grep markdown-it frontend/build || echo clean` confirms no client-side markdown lib in bundle.
