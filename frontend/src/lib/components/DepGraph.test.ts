import { render, screen, waitFor } from '@testing-library/svelte';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

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

import type { RunState, TicketGraph } from '$lib/api';
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

// Cytoscape paints to an opaque canvas, so `RUN_STATE_HEX` never reaches the DOM
// and the badge suite's className assertions cannot cover it. The node fill IS a
// cytoscape style CALLBACK though, so pull it out of the config the mocked
// constructor received and invoke it per state. `Record<RunState, string>` only
// forces every key to EXIST at compile time — a state painted the WRONG hue
// still compiles, and on this view that means an operator scanning /graph for
// stuck or failed tickets reads the colour as "fine".
describe('DepGraph run-state palette', () => {
	type StyleRule = { selector: string; style: Record<string, unknown> };
	type NodeFill = (ele: { data: (key: string) => string }) => string;

	// Mounted ONCE for the whole block: the callback closes over `RUN_STATE_HEX`,
	// so it stays valid after auto-cleanup unmounts the component — and rendering
	// per assertion would leave a pile of in-flight async `onMount`s racing that
	// cleanup.
	let fill: NodeFill;

	beforeAll(async () => {
		render(DepGraph, { props: { graph } });
		await waitFor(() => expect(cytoscapeFactory).toHaveBeenCalled());
		// The hoisted spy is declared zero-arg, so its `calls` tuples are typed
		// empty; re-view them as the config cytoscape is actually handed.
		const calls = cytoscapeFactory.mock.calls as unknown as [{ style?: StyleRule[] }][];
		const nodeRule = calls[0]?.[0]?.style?.find((rule) => rule.selector === 'node');
		const background = nodeRule?.style['background-color'];
		// Assert the shape rather than casting through it: if DepGraph ever stops
		// passing a node `background-color` CALLBACK, that is itself the
		// regression this block exists to catch, and it should fail here saying so
		// — not later as an opaque "fill is not a function".
		expect(typeof background).toBe('function');
		fill = background as NodeFill;
	});

	const fillFor = (runState: RunState): string => fill({ data: () => runState });

	// Pinned exactly, mirroring RunStateBadge's palette intent as concrete solid
	// fills — these are the six states the factory's run-state.json names, none of
	// which the graph could paint at all before this source was read.
	it.each([
		['in_progress', '#f59e0b'],
		['in_part', '#fbbf24'],
		['in_submilestone', '#fbbf24'],
		['flagged', '#ef4444'],
		['failed', '#b91c1c'],
		['needs_human', '#dc2626']
	] as const)('paints %s %s', (runState, hex) => {
		expect(fillFor(runState)).toBe(hex);
	});

	// The semantic guarantee behind those hexes: "a lane stopped and something is
	// wrong" must not be paintable as "a lane is working". Asserting the families
	// are disjoint catches a swap that still type-checks.
	it('paints every failure-ish state a red distinct from every in-progress amber', () => {
		const failure = (['flagged', 'failed', 'needs_human'] as const).map(fillFor);
		const working = (['in-flight', 'in_progress', 'in_part', 'in_submilestone'] as const).map(
			fillFor
		);

		for (const hex of [...failure, ...working]) expect(hex).toMatch(/^#[0-9a-f]{6}$/);
		expect(failure.filter((hex) => working.includes(hex))).toEqual([]);
	});

	// T80's new state. `Record<RunState, string>` only guarantees the KEY exists —
	// a wrong or duplicated hex still type-checks and still ships, which is exactly
	// what the pinning table above exists to catch.
	it('paints absent its own slate, distinct from unknown', () => {
		expect(fillFor('absent')).toBe('#64748b');
		expect(fillFor('unknown')).toBe('#94a3b8');
		expect(fillFor('absent')).not.toBe(fillFor('unknown'));
	});

	// `absent`/`unknown` both mean "no lane state to show" and must never be
	// mistakable for a lane that is working or that failed.
	it('paints the no-state pair distinctly from the working and failure families', () => {
		const noState = (['unknown', 'absent'] as const).map(fillFor);
		const others = (
			[
				'in-flight',
				'in_progress',
				'in_part',
				'in_submilestone',
				'flagged',
				'failed',
				'needs_human'
			] as const
		).map(fillFor);

		expect(noState.filter((hex) => others.includes(hex))).toEqual([]);
	});
});
