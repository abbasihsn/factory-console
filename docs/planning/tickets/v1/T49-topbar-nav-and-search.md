# [T49] Global TopBar nav (/graph, /roadmap, /search) + search box + /search results route

milestone: v1 · track: frontend · depends_on: T46, T41, T47, T48, T27, T32 · provides: NavSearch.svelte — the single TopBar navigation surface: links to /graph + /roadmap + a search box → /search route; OWNS discoverability of all three new v1 views

## Context

v1 adds cross-ticket full-text search and needs the new routes to be reachable. The current `TopBar.svelte` has NO navigation surface — only the title, project path, and a Reload button — so without this ticket the `/graph` (T47) and `/roadmap` (T48) routes would be reachable only by typing the URL. This ticket is the single owner of `TopBar.svelte` for v1: it adds a global search affordance AND nav links (Graph, Roadmap) into the header, and delivers the `/search` route that renders results from `GET /api/v1/search` (T41) reusing `TicketMiniRow`. It depends on T47/T48 so the nav links never point at routes that don't exist yet. Keeping all `TopBar` edits in one ticket avoids a parallel-merge collision on that shared header file; navigation is encapsulated in a small self-contained child so `TopBar`'s presentational unit test stays intact.

## Staged approach

1. Create `frontend/src/lib/components/NavSearch.svelte`: a nav cluster with links to `/`, `/graph`, `/roadmap` and a search `<form>`/`<input>` that on submit calls `goto('/search?q=${encodeURIComponent(value)}')` (import `goto` from `$app/navigation` — this component owns navigation, so `TopBar` stays prop-only).
2. Edit `frontend/src/lib/components/TopBar.svelte`: render `<NavSearch />` in the header row (single small addition — import + one element); do not otherwise change its existing project/Reload markup.
3. Create `frontend/src/routes/search/+page.ts`: read `q` from `url.searchParams` (URL is the source of truth, matching the index loader), call `searchTickets({ q })` when `q` is non-empty (else return empty results), delegate failures to `throwBoundaryError`; return `{ q, results }`.
4. Create `frontend/src/routes/search/+page.svelte`: show the query, an empty/no-results state, and a list of results each rendered with `TicketMiniRow` (plus a match snippet / `matchedFields` if the schema carries one) linking to `/tickets/[id]`.
5. Co-located tests for `NavSearch` (mock `$app/navigation`), the loader, and the page.

## Critical files

- `frontend/src/lib/components/NavSearch.svelte` (new — the nav + search surface)
- `frontend/src/lib/components/TopBar.svelte` (mount `<NavSearch />`)
- `frontend/src/routes/search/+page.ts` (new)
- `frontend/src/routes/search/+page.svelte` (new)

## Interface & data

- `NavSearch` submits → `goto('/search?q=...')`; `/search +page.ts` reads `?q=` → `{ q, results: SearchHit[] }` via `searchTickets({ q })`.
- Touched BY REFERENCE: the backend `SearchHit` schema (T41) consumed via the T46 generated types (`{ ticket: TicketSummary, score, matchedFields[] }`); the query param `q` mirrors the existing `/tickets` `q` convention. Reuses `TicketMiniRow` (T32) + `StatusBadge`/`RunStateBadge`; extends `TopBar` (T27) without duplicating it.
- DB ops: N/A. NFR: URL is single source of truth for `q`; optional input debounce (reuse `FiltersBar`'s debounce pattern); read-only, same-origin, no auth/cache. **Discoverability of `/graph`, `/roadmap`, `/search` is an asserted deliverable of this ticket.**

## Verification

`pnpm check` + `pnpm lint` + `pnpm test` green (`NavSearch` renders the three nav links + box; loader reads `q`; `TopBar`'s existing test still passes). Manual/e2e: with a backend on a fixture, the header shows Graph/Roadmap links + a search box; typing a term and submitting lands on `/search?q=...` with matching results, each row links to its ticket; the Graph/Roadmap links reach those routes.
