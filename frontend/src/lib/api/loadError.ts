/**
 * Shared route-load error handling: map a caught error to the SvelteKit
 * `error()` the `+error.svelte` boundary renders.
 *
 * This is the single owner of the load→boundary status policy the ticket index,
 * detail, and deps loaders share. It composes the two `ApiError`s that live in
 * separate modules on purpose — the thrown CLASS from `./errors` (branched on
 * with `instanceof`) and the render shape produced by `./contracts`'s
 * `normalizeError` — so it is NOT co-located with either (importing the class
 * into `contracts` would collide with that module's `ApiError` type alias).
 */
import { error } from '@sveltejs/kit';
import { ApiError } from './errors';
import { normalizeError } from './contracts';

// A network-failure `ApiError` carries `status: 0`, which SvelteKit's `error()`
// rejects (it only accepts 400–599); map anything outside that range to 503 so
// the boundary still renders. A non-`ApiError` failure is unexpected → 500.
const SERVICE_UNAVAILABLE = 503;
const INTERNAL_ERROR = 500;

/**
 * Convert a caught route-load error into the SvelteKit `error()` that the
 * `+error.svelte` boundary renders, and throw it (never returns).
 *
 * An {@link ApiError} keeps its backend status clamped to the 400–599 range
 * `error()` accepts (a network `status: 0` becomes a 503); anything else becomes
 * a generic 500. Both carry the {@link normalizeError} render shape. A loader
 * that maps a specific status inline (e.g. a 404 to its not-found panel) branches
 * on it BEFORE delegating the rest here.
 */
export function throwBoundaryError(err: unknown): never {
	if (err instanceof ApiError) {
		const status = err.status >= 400 && err.status <= 599 ? err.status : SERVICE_UNAVAILABLE;
		throw error(status, normalizeError(err));
	}
	throw error(INTERNAL_ERROR, normalizeError(err));
}
