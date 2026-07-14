# [T32] Dep neighborhood route `/tickets/[id]/deps`

milestone: MVP · track: frontend · depends_on: T31, T29, T23 · provides: Route rendering direct deps, direct dependents, and unresolved deps for a ticket

## Context

MVP's dep view (v1 upgrades to rendered DAG). Two clickable lists (Depends on + Depended on by) plus small Unresolved deps section for ids the backend could not resolve. Each row shows id, title, status badge, run-state badge and links to the ticket's detail page.

## Staged approach

1. `src/routes/tickets/[id]/deps/+page.ts` calls `getTicketDeps(params.id)`; on 404 return `{ notFound: true, id }`.
2. `+page.svelte`: header "Deps for {id}" + back-link to `/tickets/[id]`. Three sections: Depends on (list of `directDeps`), Depended on by (`directDependents`), Unresolved deps (`unresolvedDeps` as plain strings — no link). Empty section -> muted "None".
3. `src/lib/components/TicketMiniRow.svelte`: compact row (id link, title, `StatusBadge`, `RunStateBadge`) for the two resolved-deps lists.
4. Vitest: renders fixture `DepNeighborhood`; "None" for empty sections; not-found panel; mini-row anchors point to correct `/tickets/{id}`.

## Critical files

- `frontend/src/routes/tickets/[id]/deps/+page.ts`
- `frontend/src/routes/tickets/[id]/deps/+page.svelte`
- `frontend/src/routes/tickets/[id]/deps/+page.test.ts`
- `frontend/src/lib/components/TicketMiniRow.svelte`
- `frontend/src/lib/components/TicketMiniRow.test.ts`

## Interface & data

Consumes `GET /api/v1/tickets/{id}/deps -> DepNeighborhood { ticket, directDeps, directDependents, unresolvedDeps }`. SPA does NOT compute reverse index; trusts server.

## Verification

`pnpm dev` against `with_run_state` fixture: `/tickets/<id>/deps` renders both lists + unresolved; clicking a row navigates; the "View dep neighborhood" link on detail lands here; `pnpm test` passes.
