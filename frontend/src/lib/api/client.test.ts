import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	createTicket,
	deleteTicket,
	getGraph,
	getProject,
	getRoadmap,
	getTicket,
	getTicketDeps,
	listTickets,
	previewWrite,
	request,
	searchTickets,
	TOKEN_HEADER,
	updateTicket
} from './client';
import { ApiError } from './errors';
import type { TicketCreate, TicketUpdate, WriteResult } from './models';

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

	it('throws ApiError invalid_response when a 2xx body is not valid JSON', async () => {
		// An HTML proxy/captive-portal page served with 200: response.json() rejects.
		// The wrapper must still throw ApiError (not leak a raw SyntaxError).
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue({
			ok: true,
			status: 200,
			json: async () => {
				throw new SyntaxError('Unexpected token < in JSON');
			}
		} as unknown as Response);

		const error = await rejection(getProject());
		expect(error).toBeInstanceOf(ApiError);
		expect(error.code).toBe('invalid_response');
		expect(error.status).toBe(200);
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

	it('resolves the graph body and hits /api/v1/graph', async () => {
		const graph = {
			nodes: [
				{
					id: 'T01',
					title: 'First',
					status: 'todo',
					track: null,
					milestone: null,
					runState: 'todo'
				}
			],
			edges: [{ source: 'T02', target: 'T01' }]
		};
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(graph));

		await expect(getGraph()).resolves.toEqual(graph);
		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/graph');
	});

	it('unwraps the search envelope and hits /api/v1/search?q=... (space as +)', async () => {
		const items = [
			{
				ticket: {
					id: 'T01',
					title: 'First',
					status: 'todo',
					track: null,
					milestone: null,
					runState: 'todo',
					depCount: 0,
					dependentCount: 0
				},
				score: 3,
				matchedFields: ['title']
			}
		];
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse({ items, total: 1 }));

		await expect(searchTickets({ q: 'a b' })).resolves.toEqual(items);
		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/search?q=a+b');
	});

	it('appends the limit param when searchTickets is given one', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse({ items: [], total: 0 }));

		await searchTickets({ q: 'graph', limit: 10 });
		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/search?q=graph&limit=10');
	});

	it('resolves the present roadmap branch and hits /api/v1/roadmap', async () => {
		const roadmap = {
			path: '/repo/ROADMAP.md',
			bodyMarkdown: '# Roadmap',
			bodyHtml: '<h1>Roadmap</h1>',
			milestones: []
		};
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(roadmap));

		await expect(getRoadmap()).resolves.toEqual(roadmap);
		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/roadmap');
	});

	it('resolves the absent roadmap branch ({ present: false })', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse({ present: false }));

		await expect(getRoadmap()).resolves.toEqual({ present: false });
		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/roadmap');
	});
});

const TOKEN = 'tok-abc123';

// A create body: `provides` is required by the generated schema (it has a server
// default), so a valid draft always carries it — even as the empty string. The five
// content fields are required outright, and `criticalFiles`/`verificationCommands`
// carry the schema's `minItems: 1`, so there is no shorter valid draft than this.
const CONTENT = {
	context: 'Why this ticket exists.',
	approach: 'Create the module, then wire it up.',
	criticalFiles: ['src/a.ts'],
	interfaceData: 'N/A',
	verificationCommands: ['pnpm test']
};

const DRAFT: TicketCreate = {
	id: 'T01',
	title: 'First',
	provides: '',
	...CONTENT
};

const EDIT: TicketUpdate = { title: 'Renamed', provides: '', ...CONTENT };

// The uniform envelope every write verb answers with — apply or dry-run.
function writeResult(overrides: Partial<WriteResult> = {}): WriteResult {
	return {
		applied: true,
		ticketId: 'T01',
		changedFiles: ['docs/planning/tickets/T01.md', 'docs/planning/tickets.json'],
		diff: {
			ticketId: 'T01',
			files: [
				{
					path: 'docs/planning/tickets/T01.md',
					changeKind: 'create',
					diff: '--- /dev/null\n+++ b/T01.md\n+# First\n'
				}
			]
		},
		ticket: null,
		...overrides
	};
}

// The RequestInit the wrapper handed to fetch, typed for field assertions.
function initOf(fetchMock: ReturnType<typeof stubFetch>, call = 0): RequestInit {
	return fetchMock.mock.calls[call][1] as RequestInit;
}

function headersOf(fetchMock: ReturnType<typeof stubFetch>, call = 0): Record<string, string> {
	return initOf(fetchMock, call).headers as Record<string, string>;
}

describe('write wrappers', () => {
	it('POSTs a create with the token header, JSON content-type, and serialized body', async () => {
		const result = writeResult();
		const fetchMock = stubFetch();
		// An applying create answers 201.
		fetchMock.mockResolvedValue(jsonResponse(result, { status: 201 }));

		await expect(createTicket(DRAFT, TOKEN)).resolves.toEqual(result);

		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/tickets');
		expect(initOf(fetchMock).method).toBe('POST');
		expect(headersOf(fetchMock)['X-Factory-Write-Token']).toBe(TOKEN);
		expect(headersOf(fetchMock)['content-type']).toBe('application/json');
		expect(initOf(fetchMock).body).toBe(JSON.stringify(DRAFT));
	});

	it('exposes the token header name the backend publishes', () => {
		// Must stay byte-identical to the server's WRITE_TOKEN_HEADER / the
		// FactoryWriteToken security scheme, or every write 401s.
		expect(TOKEN_HEADER).toBe('X-Factory-Write-Token');
	});

	it('PUTs an update to the id path with the token header and body', async () => {
		const result = writeResult({ ticketId: 'T 01' });
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(result));

		await expect(updateTicket('T 01', EDIT, TOKEN)).resolves.toEqual(result);

		// The id is percent-encoded exactly like getTicket's.
		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/tickets/T%2001');
		expect(initOf(fetchMock).method).toBe('PUT');
		expect(headersOf(fetchMock)['X-Factory-Write-Token']).toBe(TOKEN);
		expect(headersOf(fetchMock)['content-type']).toBe('application/json');
		expect(initOf(fetchMock).body).toBe(JSON.stringify(EDIT));
	});

	it('DELETEs with the token header, no body, and resolves the WriteResult', async () => {
		// The server answers 200 with the full envelope (not a bodiless 204), so a
		// delete's diff renders like a create's.
		const result = writeResult({ changedFiles: ['docs/planning/tickets/T01.md'] });
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(result));

		await expect(deleteTicket('T01', TOKEN)).resolves.toEqual(result);

		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/tickets/T01');
		expect(initOf(fetchMock).method).toBe('DELETE');
		expect(headersOf(fetchMock)['X-Factory-Write-Token']).toBe(TOKEN);
		expect(initOf(fetchMock).body).toBeUndefined();
		// No body means no content-type to describe.
		expect(headersOf(fetchMock)['content-type']).toBeUndefined();
	});

	it('appends ?dryRun=true for each previewed verb, carrying the token header', async () => {
		const preview = writeResult({ applied: false, ticket: null });
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(preview));

		await expect(previewWrite({ verb: 'create', body: DRAFT }, TOKEN)).resolves.toEqual(preview);
		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/tickets?dryRun=true');
		expect(initOf(fetchMock, 0).method).toBe('POST');
		expect(headersOf(fetchMock, 0)['X-Factory-Write-Token']).toBe(TOKEN);

		await previewWrite({ verb: 'update', id: 'T01', body: EDIT }, TOKEN);
		expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/tickets/T01?dryRun=true');
		expect(initOf(fetchMock, 1).method).toBe('PUT');

		await previewWrite({ verb: 'delete', id: 'T01' }, TOKEN);
		expect(fetchMock.mock.calls[2][0]).toBe('/api/v1/tickets/T01?dryRun=true');
		expect(initOf(fetchMock, 2).method).toBe('DELETE');
		expect(headersOf(fetchMock, 2)['X-Factory-Write-Token']).toBe(TOKEN);
	});

	it('never sends dryRun on the applying wrappers', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(writeResult()));

		await createTicket(DRAFT, TOKEN);
		await updateTicket('T01', EDIT, TOKEN);
		await deleteTicket('T01', TOKEN);

		for (const call of fetchMock.mock.calls) {
			expect(String(call[0])).not.toContain('dryRun');
		}
	});

	it('percent-encodes a hostile id instead of escaping the same-origin path', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(writeResult()));

		await deleteTicket('../../etc/passwd', TOKEN);
		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/tickets/..%2F..%2Fetc%2Fpasswd');
	});

	it('still refuses an absolute path on a write request without calling fetch', async () => {
		// Writes go through the same `request()` as reads, so the same-origin guard
		// covers them too — with a method/body/token init in play.
		const fetchMock = stubFetch();

		const error = await rejection(
			request('http://evil.example/api/v1/tickets', {
				method: 'POST',
				headers: { [TOKEN_HEADER]: TOKEN },
				body: JSON.stringify(DRAFT)
			})
		);
		expect(error).toBeInstanceOf(ApiError);
		expect(error.code).toBe('invalid_request');
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it('surfaces a 401 write_token_invalid as ApiError with the envelope code', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(
			jsonResponse(
				{ error: { code: 'write_token_invalid', message: 'Invalid write token.' } },
				{ ok: false, status: 401 }
			)
		);

		const error = await rejection(createTicket(DRAFT, 'stale-token'));
		expect(error).toBeInstanceOf(ApiError);
		expect(error.code).toBe('write_token_invalid');
		expect(error.status).toBe(401);
	});

	it('surfaces the 409 not-mutable conflict as ApiError on an update', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(
			jsonResponse(
				{
					error: {
						code: 'ticket_not_mutable',
						message: 'Only todo tickets may be edited.',
						details: { runState: 'merged' }
					}
				},
				{ ok: false, status: 409 }
			)
		);

		const error = await rejection(updateTicket('T01', EDIT, TOKEN));
		expect(error).toBeInstanceOf(ApiError);
		expect(error.code).toBe('ticket_not_mutable');
		expect(error.status).toBe(409);
		expect(error.details).toEqual({ runState: 'merged' });
	});
});
