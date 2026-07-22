// Ambient types for the SvelteKit app.
// See https://svelte.dev/docs/kit/types#app
//
// `App.Error` is the shape carried by `page.error` and thrown by SvelteKit's
// `error()` helper. We model it as the normalized API error; `$lib/api/contracts`
// re-exports it as `ApiError`. Keep the fields in sync with that module's docs.
declare global {
	namespace App {
		interface Error {
			/** Machine-readable error code (from the backend envelope, or client-derived). */
			code: string;
			/** Human-readable message. */
			message: string;
			/** Optional client-derived suggestion — not part of the backend envelope. */
			hint?: string;
			/** Optional structured detail from the backend envelope. */
			details?: unknown;
		}
	}
}

export {};
