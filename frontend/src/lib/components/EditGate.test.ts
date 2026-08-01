import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { RunState } from '$lib/api';
import EditGate from '$lib/components/EditGate.svelte';

// The gate mirrors `isEditable`: only `todo` and `unknown` are writable, so those
// two are the only states with nothing to explain. `absent` is read-only for EDIT
// but still deletable, so it is banner-worthy yet says something different from the
// lane-owned states below — hence its own list.
const LANE_OWNED_STATES: RunState[] = ['in-flight', 'ready', 'merged'];
const READ_ONLY_STATES: RunState[] = [...LANE_OWNED_STATES, 'absent'];
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
			// Only what EVERY read-only state shares — WHICH writes are disabled
			// differs by state and is asserted per-branch below.
			expect(text).toContain('editing');
			expect(text).toContain('disabled');
		});
	}

	for (const runState of LANE_OWNED_STATES) {
		it(`says both writes are disabled for ${runState}`, () => {
			render(EditGate, { props: { runState } });

			const text = screen.getByRole('note').textContent?.replace(/\s+/g, ' ') ?? '';
			expect(text).toContain('editing and deleting are disabled');
		});
	}

	// The banner must not claim a refusal the server does not make. `ensure_deletable`
	// permits `absent`, so a banner that says "editing and deleting are disabled"
	// would send an operator off to hand-edit tickets.json rather than press the
	// Delete button that actually works (T80 amendment, gap 2).
	it('does not claim delete is disabled for absent', () => {
		render(EditGate, { props: { runState: 'absent' } });

		const text = screen.getByRole('note').textContent?.replace(/\s+/g, ' ') ?? '';
		expect(text).not.toContain('deleting are disabled');
		expect(text).toContain('You can still delete it.');
	});

	// T80: `absent` is read-only for a DIFFERENT reason than the lane-owned states,
	// and the banner must say so — no lane owns a ticket the run-state source never
	// listed, so the lane-ownership sentence would misdirect the operator.
	it('gives absent its own reason instead of blaming a factory lane', () => {
		render(EditGate, { props: { runState: 'absent' } });

		const text = screen.getByRole('note').textContent?.replace(/\s+/g, ' ') ?? '';
		expect(text).toContain('run-state source does not list this ticket');
		expect(text).not.toContain('a factory lane owns a ticket');
	});

	// The gate is not the only gate, and for `absent` it is not even the same gate:
	// the server would accept the delete. Claiming otherwise is worse than silence.
	it('scopes the "server would reject it anyway" claim to the edit for absent', () => {
		render(EditGate, { props: { runState: 'absent' } });

		const text = screen.getByRole('note').textContent?.replace(/\s+/g, ' ') ?? '';
		expect(text).toContain('would reject the edit anyway');
		expect(text).not.toContain('would reject the write anyway');
	});

	it('still blames the owning lane for a state a lane really did set', () => {
		render(EditGate, { props: { runState: 'merged' } });

		const text = screen.getByRole('note').textContent?.replace(/\s+/g, ' ') ?? '';
		expect(text).toContain('a factory lane owns a ticket');
	});

	for (const runState of EDITABLE_STATES) {
		it(`renders nothing for ${runState}`, () => {
			const { container } = render(EditGate, { props: { runState } });

			expect(screen.queryByRole('note')).toBeNull();
			expect(container.textContent?.trim()).toBe('');
		});
	}
});
