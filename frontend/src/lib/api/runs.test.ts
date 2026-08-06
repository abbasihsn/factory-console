import { afterEach, describe, expect, it, vi } from 'vitest';
import { getRuns } from './runs';
import { ApiError } from './errors';
import type { RunListResponse } from './models';

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

// `getRuns` lives outside `client.ts`, so `client.test.ts` does not reach it, and
// the route test mocks the `$lib/api` barrel wholesale — nothing there executes
// this module's body. Without these cases a typo'd path ships green and fails only
// at runtime.
describe('getRuns', () => {
	// `satisfies` so the fixture cannot drift from the published contract: a stale
	// field or an invalid `reason` fails the type-check here rather than letting this
	// test pass against a body the server can no longer send. Since T102 that also
	// pins the NARROWING: `data` is `Record<string, string>` holding only the server's
	// `DISCLOSED_ARTIFACT_FIELDS`, so a fixture carrying an undisclosed key — or a
	// non-string under a disclosed one — no longer type-checks as a body at all.
	const body = {
		items: [
			{
				ticketId: 'T88',
				result: {
					path: '/repo/.factory/results/T88.json',
					data: { status: 'ready', pr_url: 'https://example.test/pr/1' },
					reason: null
				},
				receipt: { path: '/repo/.factory/receipts/T88.json', data: null, reason: 'absent' as const }
			}
		],
		total: 1
	} satisfies RunListResponse;

	it('hits /api/v1/runs', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(body));

		await getRuns();

		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/runs');
	});

	it('unwraps the envelope, keeping each artifact whole', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(body));

		// Envelope-unwrapped like `listTickets`/`searchTickets`, but every per-source
		// `data`/`reason` pair survives this wrapper untouched: flattening one into a
		// boolean is the exact ambiguity the server built `ArtifactRead` to remove.
		// The narrowing of `data` happens server-side, before this module sees a byte —
		// nothing here filters, and nothing here may.
		await expect(getRuns()).resolves.toEqual(body.items);
	});

	it('resolves an empty array for an empty manifest', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse({ items: [], total: 0 } satisfies RunListResponse));

		await expect(getRuns()).resolves.toEqual([]);
	});

	it('maps a backend error envelope to an ApiError', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(
			jsonResponse(
				{ error: { code: 'internal_error', message: 'Boom.' } },
				{ ok: false, status: 500 }
			)
		);

		await expect(getRuns()).rejects.toMatchObject({ code: 'internal_error', status: 500 });
		await expect(getRuns()).rejects.toBeInstanceOf(ApiError);
	});

	it('maps an unreachable backend to a network ApiError', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

		await expect(getRuns()).rejects.toMatchObject({ code: 'network_error', status: 0 });
	});
});
