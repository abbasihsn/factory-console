import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { RunState } from '$lib/api';
import RunStateBadge from '$lib/components/RunStateBadge.svelte';

// RunStateBadge is presentational: render it with a supplied `runState` and
// snapshot the pill element per variant so the color map, humanized labels, and
// per-state title tooltip are pinned. The snapshot targets the `<span>` itself,
// not the container, so it carries no whitespace-only sibling text node (which
// the repo's trailing-whitespace hook would strip and desync). `runState` values
// are hyphenated (`in-flight`), matching the generated type.
describe('RunStateBadge', () => {
	it('renders the todo variant', () => {
		const { container } = render(RunStateBadge, { props: { runState: 'todo' } });

		expect(screen.getByText('To do')).toBeTruthy();
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-gray-100 text-gray-800"
			  title="Queued — no factory lane has started this ticket yet"
			>
			  To do
			</span>
		`);
	});

	it('renders the in-flight variant', () => {
		const { container } = render(RunStateBadge, { props: { runState: 'in-flight' } });

		expect(screen.getByText('In flight')).toBeTruthy();
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-amber-100 text-amber-800"
			  title="A factory lane is actively building this ticket"
			>
			  In flight
			</span>
		`);
	});

	it('renders the ready variant', () => {
		const { container } = render(RunStateBadge, { props: { runState: 'ready' } });

		expect(screen.getByText('Ready')).toBeTruthy();
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-green-100 text-green-800"
			  title="Built and reviewed — the PR is ready to merge"
			>
			  Ready
			</span>
		`);
	});

	it('renders the merged variant', () => {
		const { container } = render(RunStateBadge, { props: { runState: 'merged' } });

		expect(screen.getByText('Merged')).toBeTruthy();
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-violet-100 text-violet-800"
			  title="The ticket PR has been merged"
			>
			  Merged
			</span>
		`);
	});

	it('renders the unknown variant with an explanatory title tooltip', () => {
		const { container } = render(RunStateBadge, { props: { runState: 'unknown' } });

		const pill = screen.getByText('Unknown');
		expect(pill.getAttribute('title')).toBe(
			'No run-state source present, or this ticket is not in it'
		);
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-slate-100 text-slate-500"
			  title="No run-state source present, or this ticket is not in it"
			>
			  Unknown
			</span>
		`);
	});

	// The six states only the factory's run-state.json names. Each must render a
	// labelled, titled, styled pill — a state the map forgot would render an empty
	// span with `undefined` classes, which is exactly what shipped for every
	// factory state before the JSON source was read at all.
	const FACTORY_ONLY_STATES: readonly (readonly [RunState, string])[] = [
		['in_progress', 'In progress'],
		['in_part', 'In part'],
		['in_submilestone', 'In submilestone'],
		['flagged', 'Flagged'],
		['failed', 'Failed'],
		['needs_human', 'Needs human']
	];

	it.each(FACTORY_ONLY_STATES)('renders the %s variant', (runState, label) => {
		const { container } = render(RunStateBadge, { props: { runState } });

		const pill = container.querySelector('span');
		expect(screen.getByText(label)).toBeTruthy();
		expect(pill?.getAttribute('title')).toBeTruthy();
		expect(pill?.className).not.toContain('undefined');
	});

	// The three failure-ish states must be visually distinct from the in-progress
	// ones: an operator scanning the board needs "a lane stopped and something is
	// wrong" to look different from "a lane is working".
	it.each(['flagged', 'failed', 'needs_human'] as const)('paints %s as a failure pill', (state) => {
		const { container } = render(RunStateBadge, { props: { runState: state } });
		expect(container.querySelector('span')?.className).toContain('red');
	});

	it.each(['in_progress', 'in_part', 'in_submilestone'] as const)(
		'paints %s as an in-progress pill',
		(state) => {
			const { container } = render(RunStateBadge, { props: { runState: state } });
			const className = container.querySelector('span')?.className ?? '';
			expect(className).toContain('amber');
			expect(className).not.toContain('red');
		}
	);
});
