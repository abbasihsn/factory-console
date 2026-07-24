import { render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Cytoscape needs a real canvas/renderer, so it's stubbed: the constructor and its
// `use` static are spies, and the returned core exposes just the methods DepGraph
// touches (`layout().run()`, `on`, `destroy`). The dynamic `import()`s inside
// onMount resolve to these mocks. `$app/navigation` is stubbed so the tap handler
// never reaches a real router. Built via `vi.hoisted` so the spy exists before the
// hoisted `vi.mock` factory closes over it.
const { cytoscapeFactory } = vi.hoisted(() => {
	const cyCore = {
		layout: vi.fn(() => ({ run: vi.fn() })),
		on: vi.fn(),
		destroy: vi.fn()
	};
	return {
		cytoscapeFactory: Object.assign(
			vi.fn(() => cyCore),
			{ use: vi.fn() }
		)
	};
});

vi.mock('cytoscape', () => ({ default: cytoscapeFactory }));
vi.mock('cytoscape-dagre', () => ({ default: vi.fn() }));
vi.mock('dagre', () => ({ default: {} }));
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

import type { TicketGraph } from '$lib/api';
import DepGraph from '$lib/components/DepGraph.svelte';

const graph: TicketGraph = {
	nodes: [
		{ id: 'T01', title: 'Alpha', status: 'todo', runState: 'todo' },
		{ id: 'T02', title: 'Bravo', status: 'in_progress', runState: 'in-flight' },
		{ id: 'T03', title: 'Charlie', status: 'done', runState: 'merged' }
	],
	edges: [{ source: 'T02', target: 'T01' }]
};

describe('DepGraph accessible node-hook', () => {
	beforeEach(() => {
		cytoscapeFactory.mockClear();
		cytoscapeFactory.use.mockClear();
	});

	it('renders one link per node with the ticket id as name, its detail href, and data-run-state', () => {
		render(DepGraph, { props: { graph } });

		for (const node of graph.nodes ?? []) {
			const link = screen.getByRole('link', { name: node.id });
			expect(link.getAttribute('href')).toBe(`/tickets/${node.id}`);
			expect(link.getAttribute('data-run-state')).toBe(node.runState);
		}
	});

	it('renders no node links for an empty graph (guards the optional arrays)', () => {
		render(DepGraph, { props: { graph: {} } });

		expect(screen.queryAllByRole('link')).toHaveLength(0);
	});

	it('initializes cytoscape (and registers the dagre layout) on mount', async () => {
		render(DepGraph, { props: { graph } });

		await waitFor(() => expect(cytoscapeFactory).toHaveBeenCalledTimes(1));
		expect(cytoscapeFactory.use).toHaveBeenCalledTimes(1);
	});
});
