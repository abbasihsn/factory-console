import { writable, type Writable } from 'svelte/store';

/**
 * The per-session write token every mutating API call must carry.
 *
 * The server mints the token at startup and prints it to its own stderr; the user
 * pastes it into `WriteTokenPrompt`, and the write wrappers send it in the
 * `TOKEN_HEADER`. It lives in `sessionStorage` — per tab, gone when the tab
 * closes, matching the token's own per-server-session lifetime — so a reload does
 * not force a re-paste. It is deliberately NOT in `localStorage`: a token that
 * outlives the server session it belongs to is only a stale secret at rest.
 */

// Namespaced so it cannot collide with anything else on 127.0.0.1, which every
// locally-served app shares as an origin.
const STORAGE_KEY = 'factory-console:writeToken';

/**
 * Run `use` against this tab's `sessionStorage`, or return `fallback` when storage
 * is unreachable. One place owns both failure modes: no DOM storage at all (SSR /
 * prerender, where there is no `window` either) and a storage object that throws on
 * access or use (Safari private mode, storage disabled by policy). Either way the
 * store still works in memory for the life of the page.
 */
function withSessionStorage<T>(use: (storage: Storage) => T, fallback: T): T {
	try {
		if (typeof sessionStorage === 'undefined') return fallback;
		return use(sessionStorage);
	} catch {
		return fallback;
	}
}

function readStoredToken(): string | null {
	return withSessionStorage((storage) => storage.getItem(STORAGE_KEY), null);
}

/**
 * The current write token, or `null` when none is held. Hydrated once at import
 * from `sessionStorage`; use {@link setToken} / {@link clearToken} to change it so
 * the mirror stays in step (a direct `.set()` would drift from storage).
 */
export const writeToken: Writable<string | null> = writable(readStoredToken());

/**
 * Hold `token` for the rest of this tab's session.
 *
 * The pasted value is trimmed (a copied token easily carries surrounding
 * whitespace, which the server would reject), and a token that is blank after
 * trimming is no token at all — it clears instead, so nothing downstream ever
 * sends `''` as authorization.
 */
export function setToken(token: string): void {
	const trimmed = token.trim();
	if (trimmed.length === 0) {
		clearToken();
		return;
	}
	writeToken.set(trimmed);
	withSessionStorage<void>((storage) => storage.setItem(STORAGE_KEY, trimmed), undefined);
}

/**
 * Forget the token — for an explicit sign-out and for the 401 path, where the held
 * token is known bad. Removes the storage key outright rather than storing an empty
 * or `"null"` value, so the next load hydrates to `null`.
 */
export function clearToken(): void {
	writeToken.set(null);
	withSessionStorage<void>((storage) => storage.removeItem(STORAGE_KEY), undefined);
}
