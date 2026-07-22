import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
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
		expect(pill.getAttribute('title')).toBe('run-state directory not present or unresolved');
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-slate-100 text-slate-500"
			  title="run-state directory not present or unresolved"
			>
			  Unknown
			</span>
		`);
	});
});
