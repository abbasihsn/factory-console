import { afterEach, describe, expect, it, vi } from 'vitest';
import { getSpend } from './spend';
import { ApiError } from './errors';
import type { SpendResponse } from './models';

// Same `Response` stand-in and `fetch` stub as `client.test.ts`: the client only
// touches `ok`, `status`, and `json()`.
function jsonResponse(body: unknown, { ok = true, status = 200 } = {}): Response {
	return { ok, status, json: async () => body } as unknown as Response;
}

function stubFetch() {
	const fetchMock = vi.fn();
	vi.stubGlobal('fetch', fetchMock);
	return fetchMock;
}

afterEach(() => {
	vi.unstubAllGlobals();
});

// `getSpend` lives outside `client.ts`, so `client.test.ts` does not reach it, and
// the route test mocks the `$lib/api` barrel wholesale — nothing there executes
// this module's body. Without these cases a typo'd path ships green and fails only
// at runtime.
describe('getSpend', () => {
	// `satisfies` so the fixture cannot drift from the published contract: a stale
	// field or an invalid `reason` fails the type-check here rather than letting
	// this test pass against a body the server can no longer send.
	const body = {
		attribution: 'full-to-each-id',
		totals: {
			costUsd: 1.4,
			entries: 3,
			tokens: { input: 1, output: 2, cacheRead: 3, cacheCreation: 4, total: 10 }
		},
		byTicket: [
			{ ticketId: 'T84', attributedCostUsd: 1.2, entries: 2, models: ['claude-haiku-4-5'] }
		],
		byModel: [],
		byLevel: [],
		source: { found: true, read: true, path: '/repo/.factory/ledger.jsonl' },
		skipped: [{ lineNo: 7, reason: 'not_json' as const }],
		skippedOmitted: 1
	} satisfies SpendResponse;

	it('hits /api/v1/spend', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(body));

		await getSpend();

		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/spend');
	});

	it('resolves the body verbatim, keeping `source` and `skipped` intact', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(body));

		// Deliberately NOT envelope-unwrapped like `listTickets`/`searchTickets`:
		// `source` is the ONLY way to tell "no ledger" from "$0.00", so anything
		// that reshaped the body would break the view's central distinction.
		await expect(getSpend()).resolves.toEqual(body);
	});

	it('maps a backend error envelope to an ApiError', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(
			jsonResponse(
				{ error: { code: 'internal_error', message: 'Boom.' } },
				{ ok: false, status: 500 }
			)
		);

		await expect(getSpend()).rejects.toMatchObject({ code: 'internal_error', status: 500 });
		await expect(getSpend()).rejects.toBeInstanceOf(ApiError);
	});

	it('maps an unreachable backend to a network ApiError', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

		await expect(getSpend()).rejects.toMatchObject({ code: 'network_error', status: 0 });
	});
});
