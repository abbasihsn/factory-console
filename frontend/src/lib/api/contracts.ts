/**
 * REST v1 API contracts consumed by the SPA shell.
 *
 * TEMPORARY hand-written `Project`. Swap it for the generated `Project` from
 * `$lib/api/types` when T28 (`pnpm codegen`) lands, then delete the local
 * definition here.
 */

/**
 * Narrow view of the `Project` entity from `GET /api/v1/project`.
 *
 * Only the field the SPA shell consumes (`rootPath`, shown in the top bar) is
 * modelled. The full entity also carries `ticketsManifestPath`, `ticketsDir`,
 * `roadmapPath`, `runStateDir`, and `discoveredAt` — those arrive with the
 * generated type in T28.
 */
export interface Project {
	rootPath: string;
}

/**
 * Normalized, client-facing API error — the shape of `App.Error`, i.e. what
 * `page.error` carries and what `error()` throws.
 *
 * `code`/`message`/`details` mirror the backend error envelope
 * (`{ error: { code, message, details? } }`); `hint` is an OPTIONAL,
 * client-derived friendly suggestion — the backend envelope has no `hint`.
 */
export type ApiError = App.Error;

const FALLBACK_ERROR: ApiError = {
	code: 'unknown_error',
	message: 'Something went wrong.'
};

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null;
}

/**
 * Map an arbitrary error payload to a complete {@link ApiError}.
 *
 * Accepts the backend envelope (`{ error: { code, message, details? } }`), an
 * already-normalized `ApiError`, or SvelteKit's built-in `{ message }` error,
 * and falls back to a generic error for anything unrecognized.
 */
export function normalizeError(raw: unknown): ApiError {
	if (!isRecord(raw)) {
		return { ...FALLBACK_ERROR };
	}
	const envelope = isRecord(raw.error) ? raw.error : raw;
	const normalized: ApiError = {
		code: typeof envelope.code === 'string' ? envelope.code : FALLBACK_ERROR.code,
		message: typeof envelope.message === 'string' ? envelope.message : FALLBACK_ERROR.message
	};
	if (typeof envelope.hint === 'string') {
		normalized.hint = envelope.hint;
	}
	if (envelope.details !== undefined) {
		normalized.details = envelope.details;
	}
	return normalized;
}
