import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import RunStateBadge from '$lib/components/RunStateBadge.svelte';
import type { RunState } from '$lib/api/models';

// Exercise all five RunState values (note the hyphenated `in-flight`). One
// container snapshot pins each variant, plus an explicit check that every pill
// carries a non-empty `title` tooltip.
const RUN_STATES: RunState[] = ['todo', 'in-flight', 'ready', 'merged', 'unknown'];

describe('RunStateBadge', () => {
	for (const runState of RUN_STATES) {
		it(`renders a pill for run-state "${runState}"`, () => {
			const { container } = render(RunStateBadge, { props: { runState } });
			expect(container.innerHTML).toMatchSnapshot();
		});
	}

	it('sets a descriptive title tooltip on the pill', () => {
		for (const runState of RUN_STATES) {
			const { container } = render(RunStateBadge, { props: { runState } });
			const pill = container.querySelector('span');
			expect(pill?.getAttribute('title')).toBeTruthy();
		}
	});
});
