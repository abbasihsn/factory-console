import { describe, expect, it } from 'vitest';
import type { RunState } from '$lib/api';
import { isEditable } from './editability';

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
		['needs_human', false]
	] as const)('%s -> %s', (runState: RunState, expected: boolean) => {
		expect(isEditable(runState)).toBe(expected);
	});
});
