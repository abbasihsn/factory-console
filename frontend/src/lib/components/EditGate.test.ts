import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { RunState } from '$lib/api';
import EditGate from '$lib/components/EditGate.svelte';

// The banner must appear for EXACTLY the states `isEditable` rejects — it is the
// visible half of the same client-side mirror of the server write-gate.
const IMMUTABLE: RunState[] = ['in-flight', 'ready', 'merged'];
const EDITABLE: RunState[] = ['todo', 'unknown'];

describe('EditGate', () => {
	it.each(IMMUTABLE)('explains the read-only reason and names the state (%s)', (runState) => {
		render(EditGate, { props: { runState } });

		const banner = screen.getByRole('status');
		expect(banner.textContent).toContain('Read-only');
		expect(banner.textContent).toContain(runState);
	});

	it.each(EDITABLE)('renders nothing for an editable state (%s)', (runState) => {
		const { container } = render(EditGate, { props: { runState } });

		expect(screen.queryByRole('status')).toBeNull();
		expect(container.textContent?.trim()).toBe('');
	});

	it('gives each immutable state its own reason', () => {
		const reasons = IMMUTABLE.map((runState) => {
			const { unmount } = render(EditGate, { props: { runState } });
			const text = screen.getByRole('status').textContent ?? '';
			unmount();
			return text;
		});

		expect(new Set(reasons).size).toBe(IMMUTABLE.length);
	});
});
