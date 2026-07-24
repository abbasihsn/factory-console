import { render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Fully mock the API barrel so `load` never touches the network. The load imports
// the real `throwBoundaryError` from `$lib/api/loadError` (NOT the barrel) and the
// real `ApiError` CLASS from `$lib/api/errors`, so mocking the barrel down to just
// `{ getGraph }` still leaves the boundary policy + `instanceof ApiError` intact.
vi.mock('$lib/api', () => ({ getGraph: vi.fn() }));
// Rendering the populated page mounts DepGraph, whose onMount pulls in cytoscape
// and `goto`; stub all of them so nothing touches a real renderer or router. The
// constructor must return a core with the methods onMount calls (`layout().run()`,
// `on`, `destroy`) or its async body would reject unhandled.
vi.mock('cytoscape', () => ({
	default: Object.assign(
		vi.fn(() => ({ layout: vi.fn(() => ({ run: vi.fn() })), on: vi.fn(), destroy: vi.fn() })),
		{ use: vi.fn() }
	)
}));
vi.mock('cytoscape-dagre', () => ({ default: vi.fn() }));
vi.mock('dagre', () => ({ default: {} }));
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

import { getGraph } from '$lib/api';
import { ApiError } from '$lib/api/errors';
import type { TicketGraph } from '$lib/api';
import type { PageData } from './$types';
import { load } from './+page';
import Page from './+page.svelte';

const getGraphMock = vi.mocked(getGraph);

// `+page.svelte`'s `PageData` merges the root layout's `project`, so the rendered
// `data` prop must carry it too (the page itself only reads `data.graph`).
const project = {
	rootPath: '/home/dev/factory-console',
	ticketsManifestPath: '/home/dev/factory-console/docs/planning/tickets.json',
	ticketsDir: '/home/dev/factory-console/docs/planning/tickets',
	roadmapPath: null,
	runStateDir: null,
	discoveredAt: '2026-07-22T00:00:00Z'
};

const graph: TicketGraph = {
	nodes: [{ id: 'T01', title: 'Alpha', status: 'todo', runState: 'todo' }],
	edges: []
};

function pageData(g: TicketGraph): PageData {
	return { project, graph: g };
}

describe('graph load', () => {
	beforeEach(() => {
		getGraphMock.mockReset();
	});

	it('returns the graph on success', async () => {
		getGraphMock.mockResolvedValue(graph);

		const result = await load({} as never);

		expect(getGraphMock).toHaveBeenCalledTimes(1);
		expect(result).toEqual({ graph });
	});

	it('converts an ApiError to a thrown SvelteKit boundary error preserving status + code', async () => {
		getGraphMock.mockRejectedValue(
			new ApiError({ code: 'internal_error', message: 'Boom.', status: 500 })
		);

		await expect(load({} as never)).rejects.toMatchObject({
			status: 500,
			body: { code: 'internal_error', message: 'Boom.' }
		});
	});
});

describe('graph page', () => {
	it('renders the heading and the graph panel when there are nodes', () => {
		render(Page, { props: { data: pageData(graph) } });

		expect(screen.getByRole('heading', { level: 1, name: 'Dependency graph' })).toBeTruthy();
		expect(screen.queryByText('No tickets to graph yet.')).toBeNull();
	});

	it('renders the empty-state message when the graph has no nodes', () => {
		render(Page, { props: { data: pageData({}) } });

		expect(screen.getByRole('heading', { level: 1, name: 'Dependency graph' })).toBeTruthy();
		expect(screen.getByText('No tickets to graph yet.')).toBeTruthy();
	});
});
