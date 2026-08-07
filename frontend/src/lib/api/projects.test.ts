import { afterEach, describe, expect, it, vi } from 'vitest';
import { addProject, listProjects, removeProject, selectProject } from './projects';
import { TOKEN_HEADER } from './client';
import { ApiError } from './errors';
import type { CurrentSelection, ProjectListResponse, RegisteredProjectOut } from './models';

// Same `Response` stand-in and `fetch` stub as `client.test.ts`/`runs.test.ts`: the
// client only touches `ok`, `status`, and `json()`.
function jsonResponse(body: unknown, { ok = true, status = 200 } = {}): Response {
	return { ok, status, json: async () => body } as unknown as Response;
}

// A bodiless `204`, which only `DELETE /projects/{id}` answers. A real `Response`
// rejects `json()` on an empty body with a `SyntaxError`, and that rejection is the
// whole reason `removeProject` carries a `catch` — so the stub must reproduce it
// rather than resolve `undefined`, or the test would pass against a shape the
// browser never produces.
function noContentResponse(): Response {
	return {
		ok: true,
		status: 204,
		json: async () => {
			throw new SyntaxError('Unexpected end of JSON input');
		}
	} as unknown as Response;
}

function stubFetch() {
	const fetchMock = vi.fn();
	vi.stubGlobal('fetch', fetchMock);
	return fetchMock;
}

afterEach(() => {
	vi.unstubAllGlobals();
});

const TOKEN = 'test-write-token';

// `satisfies` so the fixtures cannot drift from the published contract: a stale
// field or an invalid `condition`/`reason` fails the type-check here rather than
// letting a test pass against a body the server can no longer send.
const row = {
	id: '0123456789abcdef0123456789abcdef',
	name: 'factory-console',
	path: '/repo/factory-console',
	addedAt: '2026-08-07T12:00:00Z',
	registered: true,
	selected: false,
	condition: 'ok'
} satisfies RegisteredProjectOut;

const sessionRow = {
	id: 'session',
	name: 'pinned',
	path: '/repo/pinned',
	addedAt: null,
	registered: false,
	selected: true,
	condition: 'no_factory_dir'
} satisfies RegisteredProjectOut;

// The registry wrappers live outside `client.ts`, so `client.test.ts` does not reach
// them, and the route tests mock the `$lib/api` barrel wholesale — nothing there
// executes this module's body. Without these cases a typo'd path or a dropped token
// header ships green and fails only at runtime.
describe('listProjects', () => {
	const body = { items: [sessionRow, row], total: 2 } satisfies ProjectListResponse;

	it('hits /api/v1/projects', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(body));

		await listProjects();

		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/projects');
	});

	it('unwraps the envelope into a mutable copy of `items`', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(body));

		const items = await listProjects();

		expect(items).toEqual(body.items);
		// Copied, not the generated readonly array handed straight through — the same
		// guarantee `getRuns`/`listTickets` give their callers.
		expect(items).not.toBe(body.items);
	});

	it('keeps every row, degraded ones included', async () => {
		const fetchMock = stubFetch();
		const degraded = { ...row, condition: 'path_missing' } satisfies RegisteredProjectOut;
		fetchMock.mockResolvedValue(jsonResponse({ items: [degraded], total: 1 }));

		// A deleted project must still appear WITH its condition: dropping it would
		// tell the user they never registered it.
		await expect(listProjects()).resolves.toEqual([degraded]);
	});

	it('sends no write token — the reads are ungated', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(body));

		await listProjects();

		// No init at all, so nothing can carry a header the server does not ask for.
		const headers = (fetchMock.mock.calls[0][1] as RequestInit | undefined)?.headers as
			Record<string, string> | undefined;
		expect(headers?.[TOKEN_HEADER]).toBeUndefined();
	});

	it('maps a backend error envelope to an ApiError carrying the server code', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(
			jsonResponse(
				{ error: { code: 'registry_unreadable', message: 'Cannot read the registry.' } },
				{ ok: false, status: 503 }
			)
		);

		await expect(listProjects()).rejects.toMatchObject({
			code: 'registry_unreadable',
			status: 503
		});
		await expect(listProjects()).rejects.toBeInstanceOf(ApiError);
	});

	it('maps an unreachable backend to a network ApiError', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

		await expect(listProjects()).rejects.toMatchObject({ code: 'network_error', status: 0 });
	});
});

describe('addProject', () => {
	it('POSTs the body to /api/v1/projects with the write token', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(row, { status: 201 }));

		await expect(addProject({ path: '/repo/factory-console' }, TOKEN)).resolves.toEqual(row);

		const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(path).toBe('/api/v1/projects');
		expect(init.method).toBe('POST');
		expect((init.headers as Record<string, string>)[TOKEN_HEADER]).toBe(TOKEN);
		expect((init.headers as Record<string, string>)['content-type']).toBe('application/json');
		expect(init.body).toBe(JSON.stringify({ path: '/repo/factory-console' }));
	});

	it('forwards an optional name verbatim', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(row, { status: 201 }));

		await addProject({ path: '/repo/x', name: 'My project' }, TOKEN);

		const init = fetchMock.mock.calls[0][1] as RequestInit;
		expect(init.body).toBe(JSON.stringify({ path: '/repo/x', name: 'My project' }));
	});

	it('surfaces a duplicate as its 409 code', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(
			jsonResponse(
				{ error: { code: 'duplicate_project_path', message: 'Already tracked.' } },
				{ ok: false, status: 409 }
			)
		);

		await expect(addProject({ path: '/repo/x' }, TOKEN)).rejects.toMatchObject({
			code: 'duplicate_project_path',
			status: 409
		});
	});

	it('surfaces a rejected token as `write_token_invalid`', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(
			jsonResponse(
				{ error: { code: 'write_token_invalid', message: 'Bad token.' } },
				{ ok: false, status: 401 }
			)
		);

		// The code must survive intact: it is what routes the caller to the write-token
		// prompt rather than to a generic failure.
		await expect(addProject({ path: '/repo/x' }, 'wrong')).rejects.toMatchObject({
			code: 'write_token_invalid',
			status: 401
		});
	});

	it('maps an unreachable backend to a network ApiError', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

		await expect(addProject({ path: '/repo/x' }, TOKEN)).rejects.toMatchObject({
			code: 'network_error',
			status: 0
		});
	});
});

describe('removeProject', () => {
	it('DELETEs the escaped id with the write token and resolves on the 204', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(noContentResponse());

		// The bodiless 204 is this route's success, not the shared helper's
		// `invalid_response`; a wrapper that let that error out would report every
		// successful removal as a failure.
		await expect(removeProject(row.id, TOKEN)).resolves.toBeUndefined();

		const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(path).toBe(`/api/v1/projects/${row.id}`);
		expect(init.method).toBe('DELETE');
		expect((init.headers as Record<string, string>)[TOKEN_HEADER]).toBe(TOKEN);
		// No body, so no `content-type` claiming one.
		expect((init.headers as Record<string, string>)['content-type']).toBeUndefined();
		expect(init.body).toBeUndefined();
	});

	it('escapes the id into the path, exactly like `getTicket`', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(noContentResponse());

		await removeProject('a/../b?x=1', TOKEN);

		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/projects/a%2F..%2Fb%3Fx%3D1');
	});

	it('surfaces an unknown id as its 404 code', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(
			jsonResponse(
				{ error: { code: 'project_not_registered', message: 'No such row.' } },
				{ ok: false, status: 404 }
			)
		);

		await expect(removeProject(row.id, TOKEN)).rejects.toMatchObject({
			code: 'project_not_registered',
			status: 404
		});
		await expect(removeProject(row.id, TOKEN)).rejects.toBeInstanceOf(ApiError);
	});

	it('surfaces a rejected token as `write_token_invalid`', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(
			jsonResponse(
				{ error: { code: 'write_token_invalid', message: 'Bad token.' } },
				{ ok: false, status: 401 }
			)
		);

		await expect(removeProject(row.id, 'wrong')).rejects.toMatchObject({
			code: 'write_token_invalid',
			status: 401
		});
	});

	it('still reports an unreadable 200 body as `invalid_response`', async () => {
		const fetchMock = stubFetch();
		// Only the 204 pair is absorbed; a 2xx that promised a body and could not
		// produce one is still the error the shared helper says it is.
		fetchMock.mockResolvedValue({
			ok: true,
			status: 200,
			json: async () => {
				throw new SyntaxError('Unexpected token <');
			}
		} as unknown as Response);

		await expect(removeProject(row.id, TOKEN)).rejects.toMatchObject({
			code: 'invalid_response',
			status: 200
		});
	});

	it('maps an unreachable backend to a network ApiError', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

		await expect(removeProject(row.id, TOKEN)).rejects.toMatchObject({
			code: 'network_error',
			status: 0
		});
	});
});

describe('selectProject', () => {
	const selection = {
		selected: { ...row, selected: true },
		reason: null
	} satisfies CurrentSelection;

	it('PUTs { projectId } to /api/v1/projects/current with the write token', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(jsonResponse(selection));

		await expect(selectProject(row.id, TOKEN)).resolves.toEqual(selection);

		const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(path).toBe('/api/v1/projects/current');
		expect(init.method).toBe('PUT');
		expect((init.headers as Record<string, string>)[TOKEN_HEADER]).toBe(TOKEN);
		expect((init.headers as Record<string, string>)['content-type']).toBe('application/json');
		// The id travels in the body, not the path — so there is nothing to escape.
		expect(init.body).toBe(JSON.stringify({ projectId: row.id }));
	});

	it('returns a degraded selection rather than throwing', async () => {
		const fetchMock = stubFetch();
		const degraded = {
			selected: null,
			reason: 'selected_project_missing'
		} satisfies CurrentSelection;
		fetchMock.mockResolvedValue(jsonResponse(degraded));

		// Selecting a project whose directory is gone SUCCEEDS and reports the reason;
		// that is the state an operator selects into in order to remove the row.
		await expect(selectProject(row.id, TOKEN)).resolves.toEqual(degraded);
	});

	it('surfaces a rejected token as `write_token_invalid`', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockResolvedValue(
			jsonResponse(
				{ error: { code: 'write_token_invalid', message: 'Bad token.' } },
				{ ok: false, status: 401 }
			)
		);

		await expect(selectProject(row.id, 'wrong')).rejects.toMatchObject({
			code: 'write_token_invalid',
			status: 401
		});
		await expect(selectProject(row.id, 'wrong')).rejects.toBeInstanceOf(ApiError);
	});

	it('maps an unreachable backend to a network ApiError', async () => {
		const fetchMock = stubFetch();
		fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

		await expect(selectProject(row.id, TOKEN)).rejects.toMatchObject({
			code: 'network_error',
			status: 0
		});
	});
});
