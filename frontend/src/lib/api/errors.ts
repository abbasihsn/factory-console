/**
 * The error the API client throws.
 *
 * `ApiError` (this CLASS) is what {@link module:$lib/api/client}'s wrappers throw
 * on any non-2xx response or network failure — it is a real `Error` subclass and
 * carries the HTTP `status` (0 for a request that never reached the server).
 *
 * It is deliberately distinct from `contracts.ts`'s `ApiError` (which is a TYPE
 * alias for `App.Error`): that shape is the normalized, serialisable error the
 * SvelteKit `+error.svelte` boundary renders, produced by `normalizeError`. A
 * caught {@link ApiError} can be handed to `normalizeError` to cross into that
 * render shape. The two live in different modules on purpose — one is thrown by
 * the transport, the other is rendered by the UI — so do not merge them.
 */
export interface ApiErrorInit {
	/** Machine-readable error code (backend envelope code, or a client-derived one). */
	readonly code: string;
	/** Human-readable message passed to `Error`. */
	readonly message: string;
	/** HTTP status code, or `0` when the request never reached the server. */
	readonly status: number;
	/** Optional structured detail from the backend envelope (or the underlying cause). */
	readonly details?: unknown;
}

export class ApiError extends Error {
	readonly code: string;
	readonly status: number;
	readonly details?: unknown;

	constructor({ code, message, status, details }: ApiErrorInit) {
		super(message);
		this.name = 'ApiError';
		this.code = code;
		this.status = status;
		this.details = details;
	}
}
