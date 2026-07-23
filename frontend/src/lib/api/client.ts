/**
 * Thin same-origin fetch wrapper for the REST v1 API.
 *
 * Every wrapper returns a typed body and throws {@link ApiError} on a non-2xx
 * response (normalizing the `{ error: { code, message, details? } }` envelope)
 * or on a network failure. All requests are same-origin: wrappers pass a path
 * WITHOUT a leading slash (e.g. `project`, `tickets?status=todo`), and absolute
 * URLs are refused.
 */
import { ApiError } from './errors';
import type {
	DepNeighborhood,
	Project,
	Roadmap,
	Ticket,
	TicketListResponse,
	TicketSummary
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
 * Liveness-probe shape for `GET /api/v1/health`.
 *
 * The handler now returns a typed `HealthResponse` (`ok`, `version`,
 * `projectRoot`), but the committed `types.ts` was generated while `/health` still
 * returned an untyped dict, so its response body is not typed there yet; this local
 * shape mirrors `HealthResponse` until `types.ts` is regenerated.
 */
export interface Health {
	readonly ok: boolean;
	readonly version: string;
	readonly projectRoot: string | null;
}

/** Optional filters for {@link listTickets}, forwarded as query params. */
export interface ListTicketsParams {
	readonly status?: string;
	readonly track?: string;
	readonly milestone?: string;
	readonly q?: string;
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

/** `GET /api/v1/tickets/{id}/deps` — the ticket's dependency neighborhood. */
export function getTicketDeps(id: string): Promise<DepNeighborhood> {
	return request<DepNeighborhood>(`tickets/${encodeURIComponent(id)}/deps`);
}

/** `GET /api/v1/roadmap` — whether the project has a roadmap, and its path when present. */
export function getRoadmap(): Promise<Roadmap> {
	return request<Roadmap>('roadmap');
}

/** `GET /api/v1/health` — the liveness probe. */
export function getHealth(): Promise<Health> {
	return request<Health>('health');
}
