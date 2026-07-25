# [T51] Graph-render e2e spec: /graph DAG renders, nodes colored by run-state, click-to-ticket

milestone: v1 · track: testing · depends_on: T47, T42, T33, T08 · provides: e2e coverage that /graph renders every fixture ticket as a node, each carries its correct run-state, and activating a node navigates to that ticket's detail

## Context

v1 adds a rendered dependency-graph route `/graph` (Cytoscape.js DAG colored by run-state) backed by `GET /api/v1/graph`. This spec is the browser-level acceptance for that slice: it verifies the DAG actually renders, that run-state coloring is semantically correct, and that a node is a working navigation entry point. It reuses the shared read-only console already booted by `global-setup` on `with_run_state` (a 6-node fan-out DAG spanning merged/ready/in_flight/todo), so it adds only one spec file and touches no shared harness file.

## Staged approach

1. Create `frontend/tests/e2e/graph.spec.ts`.
2. Reuse the shared console via `use.baseURL` (do NOT boot a new instance; do NOT modify `global-setup.ts` / `playwright.config.ts` — Playwright auto-discovers this `*.spec.ts`).
3. `page.goto('/graph')`; assert the graph container/heading is visible.
4. Because Cytoscape paints nodes to an opaque `<canvas>`, assert against the route's accessible companion node list (the T47 visually-hidden nav of one link per node exposing `data-run-state`): assert exactly 6 node links exist for the fixture and that each id maps to its known run-state (CAD-100=merged, CAD-118=ready, CAD-125=in_flight, CAD-131=todo, CAD-140=todo, CAD-152=todo) via the `data-run-state` attribute — the semantic "colored by run-state" assertion.
5. Assert an edge/dependency is represented (e.g. CAD-125 depends on CAD-118) via the same accessible representation.
6. Activate the CAD-125 node link and `expect(page).toHaveURL(/\/tickets\/CAD-125$/)` then assert its detail heading is visible.
7. Use only role/label/attribute locators + web-first assertions (auto-retry) — no fixed sleeps.

## Critical files

- `frontend/tests/e2e/graph.spec.ts` (new — the only file)

## Interface & data

- Consumes (by reference): the `GET /api/v1/graph` response contract (T42) and the `RunState` enum; navigation targets the existing `/tickets/{id}` route.
- REQUIRES the T47 accessible node-hook (each node a link with the ticket id as accessible name + `data-run-state`) since Cytoscape's canvas is not DOM-introspectable (fallback: `window.__cy` + `page.evaluate`).
- DB ops: N/A. NFR: determinism (web-first bounded-retry assertions, no sleeps); runs under the shared single-worker serial config against the read-only fixture — no mutation; auth N/A (127.0.0.1).

## Verification

From `frontend/`: `pnpm run e2e -- tests/e2e/graph.spec.ts`. CI runs it via the T35 Playwright step. Locally/headless without the installed wheel, set `FC_E2E_CONSOLE_CMD` to launch the in-repo package (e.g. `PYTHONPATH=server python3 -m factory_console`) so `global-setup` boots the console on `with_run_state`. Green = the DAG renders, all six nodes carry correct run-state, and node activation navigates to `/tickets/CAD-125`.
