import { describe, expect, it } from 'vitest';
import { normalizeError, type ApiError } from '$lib/api/contracts';

// `normalizeError` is the branchy, shared normalization used by the layout load
// (non-OK response body) and the +error.svelte boundary (page.error). These
// tests exercise it directly — the component smoke tests only render pre-built
// error objects and never invoke it.
const FALLBACK: ApiError = { code: 'unknown_error', message: 'Something went wrong.' };

describe('normalizeError', () => {
	it('falls back for non-record input', () => {
		expect(normalizeError(null)).toEqual(FALLBACK);
		expect(normalizeError(undefined)).toEqual(FALLBACK);
		expect(normalizeError('boom')).toEqual(FALLBACK);
		expect(normalizeError(42)).toEqual(FALLBACK);
	});

	it('unwraps the backend envelope and carries details', () => {
		const result = normalizeError({
			error: { code: 'project_not_found', message: 'No project here.', details: { path: '/x' } }
		});
		expect(result).toEqual({
			code: 'project_not_found',
			message: 'No project here.',
			details: { path: '/x' }
		});
	});

	it('accepts an already-normalized flat ApiError, preserving hint', () => {
		const flat: ApiError = {
			code: 'network_error',
			message: 'Could not reach the backend.',
			hint: 'Is the backend running?'
		};
		expect(normalizeError(flat)).toEqual(flat);
	});

	it('falls back to the generic code/message when they are missing or mistyped', () => {
		expect(normalizeError({ error: { code: 123, message: null } })).toEqual(FALLBACK);
		expect(normalizeError({})).toEqual(FALLBACK);
	});

	it('drops hint when it is not a string and details when undefined', () => {
		const result = normalizeError({ code: 'bad', message: 'nope', hint: 99 });
		expect(result).toEqual({ code: 'bad', message: 'nope' });
		expect('hint' in result).toBe(false);
		expect('details' in result).toBe(false);
	});
});
