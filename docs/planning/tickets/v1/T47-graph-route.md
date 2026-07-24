# [T47] /graph route — Cytoscape dependency DAG colored by run-state, bundled (no CDN)

milestone: v1 · track: frontend · depends_on: T46, T42, T29 · provides: a /graph route rendering the whole ticket dependency DAG with Cytoscape (bundled into the SPA), nodes colored by run-state and clickable through to /tickets/[id], with an accessible DOM node-hook for e2e

## Context

v1 adds a rendered dependency-graph view. This ticket delivers the `/graph` route and a self-contained client-only Cytoscape wrapper that consumes `GET /api/v1/graph` (T42), colors nodes by run-state (mirroring `RunStateBadge` semantics), lays them out as a DAG, and routes a node tap to that ticket's detail page. Cytoscape (+ dagre layout) is a build-time dependency bundled by Vite into the static output — no CDN, keeping the wheel self-contained.

## Staged approach

1. Add `cytoscape`, `cytoscape-dagre`, `dagre` to `frontend/package.json` devDependencies (all current deps are build-time only — the SPA is pre-built and shipped static); they get imported in app code so Vite bundles them into `_static` (no CDN).
2. Create `frontend/src/routes/graph/+page.ts`: a load that calls `getGraph()` and returns `{ graph }`, delegating failures to `throwBoundaryError` (from `$lib/api/loadError`).
3. Create `frontend/src/lib/components/DepGraph.svelte`: takes a `graph` prop; in `onMount`, register the dagre layout, build cytoscape elements from `graph.nodes/edges`, apply a stylesheet mapping node `runState` → hex color (a local `RUN_STATE_HEX` map mirroring `RunStateBadge`'s todo/in-flight/ready/merged/unknown palette, with a comment — cytoscape needs hex, not Tailwind classes), run the dagre layout, and bind node `tap` to `goto('/tickets/${id}')`; `onDestroy` → `cy.destroy()`. Guard the container ref so it only initializes client-side.
4. **Accessible DOM node-hook (required by the T51 graph e2e — Cytoscape paints to an opaque `<canvas>`):** alongside the canvas, render a visually-hidden `<nav>` of one `<a>` per node, each with the ticket id as accessible name and a `data-run-state` attribute, linking to `/tickets/[id]`. (Fallback for introspection: also expose the Cytoscape core on `window.__cy`.) This is the DOM surface e2e asserts against.
5. Create `frontend/src/routes/graph/+page.svelte`: heading + `<DepGraph graph={data.graph} />` in a bordered surface panel; empty-state message when there are no nodes.
6. Add co-located tests (route load test mocking `$lib/api`; a shallow `DepGraph` test mocking cytoscape + `$app/navigation`).

## Critical files

- `frontend/package.json` (add cytoscape/cytoscape-dagre/dagre)
- `frontend/src/routes/graph/+page.ts` (new)
- `frontend/src/routes/graph/+page.svelte` (new)
- `frontend/src/lib/components/DepGraph.svelte` (new — Cytoscape wrapper + accessible node-hook)

## Interface & data

- `+page.ts` load → `{ graph: TicketGraph }` via `getGraph()`; `DepGraph.svelte` prop `{ graph: TicketGraph }`.
- Touched BY REFERENCE: the backend `TicketGraph` schema (T42) consumed via the T46 generated types; `RunState` enum for node coloring (semantics reused from `RunStateBadge`, T29). Node tap → client-side `goto('/tickets/{id}')`.
- DB ops: N/A. NFR: client-only rendering (Cytoscape needs the DOM — init in `onMount`; `ssr` already false globally); Cytoscape bundled, no CDN; read-only, no auth/cache. **e2e contract:** the accessible node-hook (one link per node with `data-run-state`) is a deliverable, not optional.

## Verification

`pnpm build` succeeds and the bundle contains Cytoscape (no external CDN request); `pnpm check` + `pnpm lint` green; `pnpm test` passes the co-located tests. Manual/e2e: with a backend on a fixture, navigate (by click) to `/graph` — DAG renders, nodes carry run-state colors, clicking a node lands on its `/tickets/[id]`; the accessible node list exists with correct ids + `data-run-state`. Full render assertion lives in T51.
