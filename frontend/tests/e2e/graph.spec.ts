import { test, expect } from '@playwright/test';

// Browser-level acceptance for the `/graph` dependency-graph route (Cytoscape.js
// DAG colored by run-state), served by a real factory-console booted in
// global-setup on the read-only `with_run_state` fixture (use.baseURL points at
// it). Cytoscape paints its nodes to an OPAQUE <canvas>, so the DOM surface this
// spec queries is DepGraph's visually-hidden companion nav: one <a> per node with
// the ticket id as its accessible name and a `data-run-state` attribute — the
// "colored by run-state" semantic. Edges aren't in that nav (it lists only
// NODES), so the one dependency assertion falls back to the documented
// `window.__cy` Cytoscape core, which DepGraph sets only AFTER an async
// cytoscape import in onMount — hence the bounded `expect.poll` (auto-retry, not
// a sleep). Locators are role/label/attribute based and every assertion is
// web-first (auto-retrying), so no fixed sleeps are needed.

// The fixture's six nodes and their run-state (the RunState enum uses HYPHENS).
// Driven as a table so the per-node check is one loop, not six copy-pasted blocks.
const NODES: ReadonlyArray<{ id: string; runState: string }> = [
	{ id: 'CAD-100', runState: 'merged' },
	{ id: 'CAD-118', runState: 'ready' },
	{ id: 'CAD-125', runState: 'in-flight' },
	{ id: 'CAD-131', runState: 'todo' },
	{ id: 'CAD-140', runState: 'todo' },
	{ id: 'CAD-152', runState: 'todo' }
];

// CAD-125 depends on CAD-118 → DepGraph builds a cytoscape edge with this id
// (`${source}->${target}`, source depends on target).
const DEP_EDGE_ID = 'CAD-125->CAD-118';

test('graph: renders 6 run-state-colored nodes, a dependency edge, and click-to-ticket', async ({
	page
}) => {
	await test.step('/graph renders the dependency-graph heading', async () => {
		await page.goto('/graph');
		await expect(page.getByRole('heading', { name: 'Dependency graph', level: 1 })).toBeVisible();
	});

	const nodeNav = page.getByRole('navigation', { name: 'Ticket dependency nodes' });

	await test.step('the accessible node nav lists exactly the 6 fixture nodes', async () => {
		await expect(nodeNav.getByRole('link')).toHaveCount(NODES.length);
	});

	await test.step('each node link carries its correct run-state (the coloring semantic)', async () => {
		for (const { id, runState } of NODES) {
			await expect(nodeNav.getByRole('link', { name: id, exact: true })).toHaveAttribute(
				'data-run-state',
				runState
			);
		}
	});

	await test.step('the CAD-125 → CAD-118 dependency edge is represented in the graph', async () => {
		// Edges live only in the cytoscape core, not the node nav. `__cy` is set after
		// the async cytoscape import in onMount, so poll (bounded, not a sleep) with a
		// generous timeout until the edge resolves.
		await expect
			.poll(
				() =>
					page.evaluate((edgeId) => {
						const core = (
							window as unknown as { __cy?: { getElementById(id: string): { length: number } } }
						).__cy;
						return core?.getElementById(edgeId).length === 1;
					}, DEP_EDGE_ID),
				{ timeout: 10_000 }
			)
			.toBe(true);
	});

	await test.step('activating the CAD-125 node navigates to its ticket detail page', async () => {
		// The node nav is sr-only (1px/clipped) and sits UNDER the full-size cytoscape
		// canvas, which intercepts a hit-tested pointer click — so activate the real
		// anchor by dispatching a bubbling click, which SvelteKit's client router
		// catches and routes exactly as a user click would.
		await nodeNav.getByRole('link', { name: 'CAD-125', exact: true }).dispatchEvent('click');
		await expect(page).toHaveURL(/\/tickets\/CAD-125$/);
		// The page header h1 and the MarkdownBody both carry the title — assert the first.
		await expect(
			page.getByRole('heading', { name: 'Daily check-in REST endpoints', level: 1 }).first()
		).toBeVisible();
	});
});
