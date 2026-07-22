import { afterEach, describe, expect, it, vi } from 'vitest';
import { getProject, getRoadmap, getTicket, getTicketDeps, listTickets, request } from './client';
import { ApiError } from './errors';

// Minimal `Response` stand-in: the client only touches `ok`, `status`, `json()`.
function jsonResponse(body: unknown, { ok = true, status = 200 } = {}): Response {
	return { ok, status, json: async () => body } as unknown as Response;
}

// Stub `fetch`; return the mock so tests can assert on the URL it was called with.
function stubFetch() {
	const fetchMock = vi.fn();
	vi.stubGlobal('fetch', fetchMock);
	return fetchMock;
}

// Await a promise expected to reject and return the thrown `ApiError` (typed, so
// its fields can be asserted). Fails loudly if the promise resolves instead.
async function rejection(promise: Promise<unknown>): Promise<ApiError> {
	try {
		await promise;
	} catch (error) {
		return error as ApiError;
	}
	throw new Error('expected the promise to reject, but it resolved');
}

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('API client', () => {
	it('resolves a success GET to the parsed body and hits /api/v1/project', async () => {
		const project = {
			rootPath: '/repo',
			ticketsManifestPath: '/repo/tickets.json',
			ticketsDir: '/repo/tickets',
			roadmapPath: null,
			runStateDir: null,
			discoveredAt: '2026-01-01T00:00:00Z'
		};
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(project));

		await expect(getProject()).resolves.toEqual(project);
		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/project');
	});

	it('throws ApiError with the envelope code/status/message on a 404', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(
			jsonResponse(
				{ error: { code: 'ticket_not_found', message: 'No such ticket.' } },
				{ ok: false, status: 404 }
			)
		);

		const error = await rejection(getTicket('nope'));
		expect(error).toBeInstanceOf(ApiError);
		expect(error.code).toBe('ticket_not_found');
		expect(error.status).toBe(404);
		expect(error.message).toBe('No such ticket.');
	});

	it('throws ApiError with code network_error when fetch rejects', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

		const error = await rejection(getProject());
		expect(error).toBeInstanceOf(ApiError);
		expect(error.code).toBe('network_error');
		expect(error.status).toBe(0);
	});

	it('falls back to http_error and the status when the error body is missing', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(null, { ok: false, status: 500 }));

		const error = await rejection(getProject());
		expect(error).toBeInstanceOf(ApiError);
		expect(error.code).toBe('http_error');
		expect(error.status).toBe(500);
	});

	it('URL-encodes listTickets query params and unwraps the items envelope', async () => {
		const items = [
			{
				id: 'T01',
				title: 'First',
				status: 'todo',
				track: null,
				milestone: null,
				runState: 'todo',
				depCount: 0,
				dependentCount: 0
			}
		];
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse({ items, total: 1 }));

		await expect(listTickets({ status: 'todo', q: 'a b' })).resolves.toEqual(items);
		// URLSearchParams encodes the space as '+'.
		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/tickets?status=todo&q=a+b');
	});

	it('omits empty/undefined params so listTickets hits the bare /api/v1/tickets', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse({ items: [], total: 0 }));

		await listTickets({ status: '', track: undefined });
		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/tickets');
	});

	it('refuses an absolute-URL path (same-origin guard) without calling fetch', async () => {
		const fetchMock = stubFetch();

		const error = await rejection(request('http://evil.example/api/v1/project'));
		expect(error).toBeInstanceOf(ApiError);
		expect(error.code).toBe('invalid_request');
		expect(error.status).toBe(0);
		expect(fetchMock).not.toHaveBeenCalled();

		// A protocol-relative reference is refused too.
		await expect(request('//evil.example/api')).rejects.toBeInstanceOf(ApiError);
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it('builds the roadmap and deps URLs (with id encoded)', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse({}));

		await getRoadmap();
		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/roadmap');

		await getTicketDeps('T 01');
		expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/tickets/T%2001/deps');
	});
});
