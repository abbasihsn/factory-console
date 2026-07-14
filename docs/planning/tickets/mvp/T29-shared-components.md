# [T29] Shared components: StatusBadge, RunStateBadge, MarkdownBody

milestone: MVP · track: frontend · depends_on: T28 · provides: `StatusBadge.svelte`, `RunStateBadge.svelte`, `MarkdownBody.svelte` with Vitest snapshots — the ONE place the SPA uses `{@html}` (sanitized server-side, T14)

## Context

All three routes need identical rendering of status, run-state, and markdown bodies. Extracting now keeps route tickets small and prevents drift. `MarkdownBody` concentrates the `{@html}` trust boundary in one auditable component.

## Staged approach

1. `StatusBadge.svelte`: props `{ status: string }`; maps known statuses (`todo`, `in-progress`, `done`, `blocked`) to Tailwind color classes (gray, amber, green, red); unknown -> neutral pill with raw string.
2. `RunStateBadge.svelte`: props `{ runState: 'todo'|'in_flight'|'ready'|'merged'|'unknown' }`; similar color map; `unknown` -> muted; `title` tooltip explaining state.
3. `MarkdownBody.svelte`: props `{ html: string }`; renders `<div class="prose ...">{@html html}</div>`; one-line comment linking to REST v1 contract clause "Server-rendered HTML only; do NOT introduce client-side markdown lib."
4. Vitest with `@testing-library/svelte`: snapshot per `StatusBadge` variant; snapshot per `RunStateBadge` variant; `MarkdownBody` test renders fixture HTML and asserts exact HTML present in DOM.

## Critical files

- `frontend/src/lib/components/StatusBadge.svelte`
- `frontend/src/lib/components/StatusBadge.test.ts`
- `frontend/src/lib/components/RunStateBadge.svelte`
- `frontend/src/lib/components/RunStateBadge.test.ts`
- `frontend/src/lib/components/MarkdownBody.svelte`
- `frontend/src/lib/components/MarkdownBody.test.ts`

## Interface & data

Consumes `Ticket.status / TicketSummary.status` (string; enum passthrough); `TicketSummary.runState / Ticket`-derived `runState` (`RunState` enum); `Ticket.bodyHtml` (server-sanitized per REST v1). NFR: `{@html}` used here and ONLY here.

## Verification

`pnpm test` passes badge snapshots + `MarkdownBody` injection; `grep -r '{@html' frontend/src` shows exactly one match inside `MarkdownBody.svelte`.
