import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import StatusBadge from '$lib/components/StatusBadge.svelte';

// StatusBadge is presentational: render it with a supplied `status` string and
// snapshot the pill element per variant so the known-status color map and the
// neutral unknown fallback are both pinned. The snapshot targets the `<span>`
// itself, not the container, so it carries no whitespace-only sibling text node
// (which the repo's trailing-whitespace hook would strip and desync). The raw
// status text is always the label.
describe('StatusBadge', () => {
	it('renders the todo variant', () => {
		const { container } = render(StatusBadge, { props: { status: 'todo' } });

		expect(screen.getByText('todo')).toBeTruthy();
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-gray-100 text-gray-800"
			>
			  todo
			</span>
		`);
	});

	it('renders the in-progress variant', () => {
		const { container } = render(StatusBadge, { props: { status: 'in-progress' } });

		expect(screen.getByText('in-progress')).toBeTruthy();
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-amber-100 text-amber-800"
			>
			  in-progress
			</span>
		`);
	});

	it('renders the done variant', () => {
		const { container } = render(StatusBadge, { props: { status: 'done' } });

		expect(screen.getByText('done')).toBeTruthy();
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-green-100 text-green-800"
			>
			  done
			</span>
		`);
	});

	it('renders the blocked variant', () => {
		const { container } = render(StatusBadge, { props: { status: 'blocked' } });

		expect(screen.getByText('blocked')).toBeTruthy();
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-red-100 text-red-800"
			>
			  blocked
			</span>
		`);
	});

	it('renders an unknown status as a neutral pill showing the raw string', () => {
		const { container } = render(StatusBadge, { props: { status: 'weird' } });

		expect(screen.getByText('weird')).toBeTruthy();
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-slate-100 text-slate-700"
			>
			  weird
			</span>
		`);
	});
});
