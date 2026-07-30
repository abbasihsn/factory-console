import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { RunState } from '$lib/api';
import EditGate from '$lib/components/EditGate.svelte';

// The gate mirrors `isEditable`: only `todo` and `unknown` are writable, so those
// two are the only states with nothing to explain.
const READ_ONLY_STATES: RunState[] = ['in-flight', 'ready', 'merged'];
const EDITABLE_STATES: RunState[] = ['todo', 'unknown'];

describe('EditGate', () => {
	for (const runState of READ_ONLY_STATES) {
		it(`explains why ${runState} is read-only`, () => {
			render(EditGate, { props: { runState } });

			// Markup line breaks are not content: collapse whitespace so the assertion
			// is about what the banner SAYS, not how the template wraps.
			const text = screen.getByRole('note').textContent?.replace(/\s+/g, ' ') ?? '';
			expect(text).toContain('Read-only.');
			// The raw run-state value, so the banner names the same thing the server
			// gate and the run-state directory do.
			expect(text).toContain(runState);
			expect(text).toContain('editing and deleting are disabled');
		});
	}

	for (const runState of EDITABLE_STATES) {
		it(`renders nothing for ${runState}`, () => {
			const { container } = render(EditGate, { props: { runState } });

			expect(screen.queryByRole('note')).toBeNull();
			expect(container.textContent?.trim()).toBe('');
		});
	}
});
