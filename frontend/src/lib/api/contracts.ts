/**
 * REST v1 API contracts consumed by the SPA shell.
 *
 * `Project` is re-exported from the generated `$lib/api/types` (via `./models`)
 * now that T28's `pnpm codegen` has landed. The SPA shell only reads `rootPath`,
 * but consumers get the full generated entity.
 */
export type { Project } from './models';

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
