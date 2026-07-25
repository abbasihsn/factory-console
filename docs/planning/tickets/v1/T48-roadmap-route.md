# [T48] /roadmap route — full ROADMAP body + structured milestones with checkbox state

milestone: v1 · track: frontend · depends_on: T46, T43, T29, T32 · provides: a /roadmap route rendering the server-sanitized ROADMAP body via MarkdownBody plus structured milestone sections listing each milestone's tickets and checkbox state

## Context

The MVP roadmap endpoint was presence-only; v1 expands it (T43) to return the rendered body and structured milestones. This ticket delivers the `/roadmap` route that renders that body through the existing `MarkdownBody` (the one sanctioned `{@html}` boundary — no client-side markdown) and lists the structured milestones, reusing `TicketMiniRow`/`StatusBadge` for each milestone's tickets so nothing is duplicated.

## Staged approach

1. Create `frontend/src/routes/roadmap/+page.ts`: load calls `getRoadmap()`; on the absent case return `{ present: false }`; on present return `{ present: true, roadmap }`; delegate unexpected failures to `throwBoundaryError`.
2. Create `frontend/src/lib/components/MilestoneSection.svelte`: takes a milestone `{ name, items }` prop and renders a titled section — a small checkbox glyph per item reflecting its `done` state (read-only display, not an input) and, when the item carries a `ticketId`, a `TicketMiniRow` (or `StatusBadge` + id link) linking to `/tickets/[id]`.
3. Create `frontend/src/routes/roadmap/+page.svelte`: friendly "no roadmap" panel when `present=false`; otherwise render the milestone sections (`#each` over `roadmap.milestones` with `MilestoneSection`) followed by `<MarkdownBody html={roadmap.bodyHtml} />` for the full rendered body.
4. Add co-located route + `MilestoneSection` tests.

## Critical files

- `frontend/src/routes/roadmap/+page.ts` (new)
- `frontend/src/routes/roadmap/+page.svelte` (new)
- `frontend/src/lib/components/MilestoneSection.svelte` (new)

## Interface & data

- `+page.ts` → `{ present: false } | { present: true, roadmap }` via `getRoadmap()`; `MilestoneSection` prop `{ milestone: { name, items } }`.
- Touched BY REFERENCE: the expanded backend `Roadmap` schema (T43) consumed via the T46 generated types — `{ path, bodyMarkdown, bodyHtml, milestones[] }`, each milestone `{ name, items[] }`, each item `{ text, ticketId?, done? }`. Reuses `MarkdownBody` (server-sanitized `bodyHtml` only, T29), `TicketMiniRow` (T32), `StatusBadge`.
- DB ops: N/A. NFR: `{@html}` confined to `MarkdownBody`; read-only, no auth/cache.

## Verification

`pnpm check` + `pnpm lint` + `pnpm test` green (route + `MilestoneSection` unit tests). Manual/e2e: with a backend on a fixture that has a `ROADMAP`, navigate (by click) to `/roadmap` — milestone sections list their tickets with checkbox state and the rendered body appears; a project with no roadmap shows the empty panel.
