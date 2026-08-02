import { describe, expect, it } from 'vitest';
import type { RunState } from '$lib/api';
import { isDeletable, isEditable } from './editability';

describe('isEditable', () => {
	it.each([
		['todo', true],
		['unknown', true],
		['in-flight', false],
		['ready', false],
		['merged', false],
		// The six states only the factory's run-state.json names. `isEditable` is
		// an allowlist, so it should already refuse them — asserted rather than
		// assumed, because "an allowlist covers it" is exactly the kind of claim
		// that quietly stops being true.
		['in_progress', false],
		['in_part', false],
		['in_submilestone', false],
		['flagged', false],
		['failed', false],
		['needs_human', false],
		// T80: absent is refused too — distinct from unknown, which stays
		// editable (no run-state source at all vs. a source that resolved and
		// simply does not list this ticket).
		['absent', false],
		// T80 amendment 2: `unreadable` is refused too, and for the opposite reason to
		// `unknown` — a source that EXISTS and could not be read may be hiding a
		// `merged` marker, so the UI must not offer an edit the server will 409.
		['unreadable', false]
	] as const)('%s -> %s', (runState: RunState, expected: boolean) => {
		expect(isEditable(runState)).toBe(expected);
	});
});

describe('isDeletable', () => {
	it.each([
		// Everything `isEditable` allows.
		['todo', true],
		['unknown', true],
		// PLUS `absent`, and only `absent`: the server's `DELETABLE_STATES` is
		// `MUTABLE_STATES` widened by exactly one member, so that create can always
		// be undone (T80 amendment, gap 2).
		['absent', true],
		// Every state a factory lane actually owns still refuses the delete — this is
		// the assertion that fails if the widening is ever done by dropping the
		// allowlist rather than by adding one member to it.
		['in-flight', false],
		['ready', false],
		['merged', false],
		['in_progress', false],
		['in_part', false],
		['in_submilestone', false],
		['flagged', false],
		['failed', false],
		['needs_human', false],
		// And the widening stops AT `absent`: `unreadable` is in neither server
		// allowlist, because "the source could not be read" proves nothing about
		// whether the factory tracks this ticket, where "the source does not list it"
		// proves the delete is harmless (T80 amendment 2).
		['unreadable', false]
	] as const)('%s -> %s', (runState: RunState, expected: boolean) => {
		expect(isDeletable(runState)).toBe(expected);
	});

	// The two predicates must differ on exactly one state. Asserted as a relation
	// rather than as two lists, so widening `isEditable` to cover `absent` — the
	// thing the server deliberately does NOT do — fails here.
	it('differs from isEditable on absent alone', () => {
		const states: RunState[] = [
			'todo',
			'unknown',
			'absent',
			'in-flight',
			'ready',
			'merged',
			'in_progress',
			'in_part',
			'in_submilestone',
			'flagged',
			'failed',
			'needs_human',
			'unreadable'
		];
		const differing = states.filter((s) => isDeletable(s) !== isEditable(s));

		expect(differing).toEqual(['absent']);
	});
});
