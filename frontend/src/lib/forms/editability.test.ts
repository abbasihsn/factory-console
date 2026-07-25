import { describe, expect, it } from 'vitest';
import type { RunState } from '$lib/api';
import { isEditable } from './editability';

describe('isEditable', () => {
	it.each([
		['todo', true],
		['unknown', true],
		['in-flight', false],
		['ready', false],
		['merged', false]
	] as const)('%s -> %s', (runState: RunState, expected: boolean) => {
		expect(isEditable(runState)).toBe(expected);
	});
});
