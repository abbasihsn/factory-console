/**
 * Thin same-origin fetch wrapper for the REST v1 API.
 *
 * Every wrapper returns a typed body and throws {@link ApiError} on a non-2xx
 * response (normalizing the `{ error: { code, message, details? } }` envelope)
 * or on a network failure. All requests are same-origin: wrappers pass a path
 * WITHOUT a leading slash (e.g. `project`, `tickets?status=todo`), and absolute
 * URLs are refused.
 *
 * The mutating wrappers at the bottom of the file add this session's write token
 * in {@link TOKEN_HEADER} and otherwise go through the very same {@link request},
 * so writes inherit the same guard, timeout, and error envelope as reads.
 */
import { ApiError } from './errors';
import type {
	DepNeighborhood,
	Health,
	Project,
	Roadmap,
	SearchHit,
	SearchResponse,
	Ticket,
	TicketCreate,
	TicketGraph,
	TicketListResponse,
	TicketSummary,
	TicketUpdate,
	WritePreview,
	WriteResult
} from './models';

const API_V1_PREFIX = '/api/v1';

// Abort a request if the backend accepts the socket but never responds, so a hung
// connection surfaces as the `network_error` envelope instead of a promise that
// never settles. Mirrors the layout loader's PROJECT_FETCH_TIMEOUT_MS.
const REQUEST_TIMEOUT_MS = 10_000;

// An absolute reference: a URL scheme (`http:`, `file:`, …) or a
// protocol-relative `//host` prefix. Same-origin paths never match.
const ABSOLUTE_REFERENCE = /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i;

/**
 * Header carrying this session's write token on every mutating request.
 *
 * MUST stay byte-identical to the backend's `WRITE_TOKEN_HEADER`
 * (`server/factory_console/config.py`), which the OpenAPI document publishes as
 * the `FactoryWriteToken` apiKey security scheme; a mismatch makes every write
 * fail with the 401 `write_token_invalid` envelope.
 */
export const TOKEN_HEADER = 'X-Factory-Write-Token';

// Query flag every write verb accepts to preview instead of apply. The response
// is the same `WriteResult` envelope with `applied: false`.
const DRY_RUN_QUERY = 'dryRun=true';

/** Optional filters for {@link listTickets}, forwarded as query params. */
export interface ListTicketsParams {
	readonly status?: string;
	readonly track?: string;
	readonly milestone?: string;
	readonly q?: string;
}

/** Query for {@link searchTickets}: the required full-text `q` and an optional `limit`. */
export interface SearchParams {
	readonly q: string;
	readonly limit?: number;
}

/**
 * Resolved filter state for the tickets index — the four {@link ListTicketsParams}
 * fields with every value present (an empty string means "unset"). The URL is the
 * source of truth, so each param is always a defined string; deriving it as
 * `Required<ListTicketsParams>` ties the UI filter shape to the API params, so a
 * new filter is declared in exactly one place instead of three.
 */
export type Filters = Required<ListTicketsParams>;

/**
 * Same-origin fetch + envelope normalization behind every wrapper.
 *
 * Exported for the co-located tests (the same-origin guard has no public
 * wrapper that can reach it); it is intentionally NOT re-exported from the
 * package barrel (`./index`) — callers use the endpoint wrappers below.
 */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
	if (ABSOLUTE_REFERENCE.test(path)) {
		throw new ApiError({
			code: 'invalid_request',
			message: `Refusing non-relative API path: ${path}`,
			status: 0
		});
	}

	let response: Response;
	try {
		response = await fetch(`${API_V1_PREFIX}/${path}`, {
			...init,
			// A caller-supplied signal wins; otherwise default to a timeout so no
			// request can hang forever. A fired timeout rejects fetch with a
			// TimeoutError, which the catch below maps to `network_error`.
			signal: init?.signal ?? AbortSignal.timeout(REQUEST_TIMEOUT_MS)
		});
	} catch (cause) {
		throw new ApiError({
			code: 'network_error',
			message: 'Could not reach the backend.',
			status: 0,
			details: cause
		});
	}

	if (!response.ok) {
		const body = (await response.json().catch(() => null)) as {
			error?: { code?: string; message?: string; details?: unknown };
		} | null;
		const envelope = body?.error;
		throw new ApiError({
			code: envelope?.code ?? 'http_error',
			message: envelope?.message ?? `Request failed with status ${response.status}.`,
			status: response.status,
			details: envelope?.details
		});
	}

	// Parse the success body inside the guard too: a 2xx that isn't the expected
	// JSON (e.g. an HTML proxy/captive-portal page served with 200) would otherwise
	// throw a raw SyntaxError, breaking the "wrappers always throw ApiError" contract.
	try {
		return (await response.json()) as T;
	} catch (cause) {
		throw new ApiError({
			code: 'invalid_response',
			message: 'The backend returned an unreadable response.',
			status: response.status,
			details: cause
		});
	}
}

/** `GET /api/v1/project` — the discovered target project. */
export function getProject(): Promise<Project> {
	return request<Project>('project');
}

/** `GET /api/v1/tickets` — ticket summaries matching the given filters (envelope unwrapped). */
export async function listTickets(params: ListTicketsParams = {}): Promise<TicketSummary[]> {
	const query = new URLSearchParams();
	for (const [key, value] of Object.entries(params)) {
		if (value !== undefined && value !== '') {
			query.set(key, value);
		}
	}
	const queryString = query.toString();
	const response = await request<TicketListResponse>(
		queryString ? `tickets?${queryString}` : 'tickets'
	);
	// `total === items.length` in the MVP (no pagination); copy the immutable
	// array so the caller gets a mutable `TicketSummary[]`.
	return [...response.items];
}

/** `GET /api/v1/tickets/{id}` — the full ticket with run-state resolved. */
export function getTicket(id: string): Promise<Ticket> {
	return request<Ticket>(`tickets/${encodeURIComponent(id)}`);
}

/** `GET /api/v1/graph` — the whole-project dependency DAG (nodes + edges). */
export function getGraph(): Promise<TicketGraph> {
	return request<TicketGraph>('graph');
}

/** `GET /api/v1/search` — ranked hits for the full-text query (envelope unwrapped like {@link listTickets}). */
export async function searchTickets(params: SearchParams): Promise<SearchHit[]> {
	const query = new URLSearchParams({ q: params.q });
	if (params.limit !== undefined) {
		query.set('limit', String(params.limit));
	}
	const response = await request<SearchResponse>(`search?${query.toString()}`);
	// Drop the `{ items, total }` envelope and hand back a mutable copy, exactly
	// like `listTickets`.
	return [...response.items];
}

/** `GET /api/v1/tickets/{id}/deps` — the ticket's dependency neighborhood. */
export function getTicketDeps(id: string): Promise<DepNeighborhood> {
	return request<DepNeighborhood>(`tickets/${encodeURIComponent(id)}/deps`);
}

/** `GET /api/v1/roadmap` — the roadmap document when present, else the absence marker. */
export function getRoadmap(): Promise<Roadmap> {
	return request<Roadmap>('roadmap');
}

/** `GET /api/v1/health` — the liveness probe. */
export function getHealth(): Promise<Health> {
	return request<Health>('health');
}

/**
 * One mutation, as a discriminated union over the three write verbs.
 *
 * This is the shape {@link previewWrite} takes, so a preview is described exactly
 * like the apply it previews and the compiler enforces which fields each verb
 * needs: `create` carries a body and no id (the id lives IN the body), `update`
 * carries both, `delete` carries only an id. A flat
 * `previewWrite(verb, id?, body?, token)` tuple could not express that — it would
 * accept `('delete', undefined, body, token)` and other nonsense.
 */
export type WriteRequest =
	| { readonly verb: 'create'; readonly body: TicketCreate }
	| { readonly verb: 'update'; readonly id: string; readonly body: TicketUpdate }
	| { readonly verb: 'delete'; readonly id: string };

// The path, method, and serialized body of one write — the ONLY place the three
// verbs differ. Ids are `encodeURIComponent`-escaped exactly like `getTicket`, so
// an id can never break out of the same-origin path.
function writeTarget(write: WriteRequest): {
	readonly path: string;
	readonly method: 'POST' | 'PUT' | 'DELETE';
	readonly body?: string;
} {
	switch (write.verb) {
		case 'create':
			return { path: 'tickets', method: 'POST', body: JSON.stringify(write.body) };
		case 'update':
			return {
				path: `tickets/${encodeURIComponent(write.id)}`,
				method: 'PUT',
				body: JSON.stringify(write.body)
			};
		case 'delete':
			return { path: `tickets/${encodeURIComponent(write.id)}`, method: 'DELETE' };
	}
}

/**
 * Send one write through the shared {@link request}, applying it or previewing it.
 *
 * Every write goes through here so the same-origin refusal, the request timeout,
 * and the `ApiError` envelope normalization apply to mutations exactly as they do
 * to reads — there is no second fetch path.
 */
function sendWrite(
	write: WriteRequest,
	token: string,
	{ dryRun }: { dryRun: boolean }
): Promise<WriteResult> {
	const { path, method, body } = writeTarget(write);
	return request<WriteResult>(dryRun ? `${path}?${DRY_RUN_QUERY}` : path, {
		method,
		headers: {
			[TOKEN_HEADER]: token,
			// A content-type only describes a body; DELETE sends none.
			...(body === undefined ? {} : { 'content-type': 'application/json' })
		},
		body
	});
}

/** `POST /api/v1/tickets` — create the ticket and return the applied result (`applied: true`). */
export function createTicket(body: TicketCreate, token: string): Promise<WriteResult> {
	return sendWrite({ verb: 'create', body }, token, { dryRun: false });
}

/** `PUT /api/v1/tickets/{id}` — overwrite the ticket and return the applied result. */
export function updateTicket(id: string, body: TicketUpdate, token: string): Promise<WriteResult> {
	return sendWrite({ verb: 'update', id, body }, token, { dryRun: false });
}

/**
 * `DELETE /api/v1/tickets/{id}` — delete the ticket.
 *
 * Resolves to the same {@link WriteResult} envelope as the other two verbs (the
 * server answers `200` with a body, not a bodiless `204`), so a delete's diff
 * renders in the same confirmation view as a create or an edit.
 */
export function deleteTicket(id: string, token: string): Promise<WriteResult> {
	return sendWrite({ verb: 'delete', id }, token, { dryRun: false });
}

/**
 * The same verb with `?dryRun=true` — the diff that WOULD be written, having
 * written nothing (`applied: false`, `ticket: null`).
 */
export function previewWrite(write: WriteRequest, token: string): Promise<WritePreview> {
	return sendWrite(write, token, { dryRun: true });
}
