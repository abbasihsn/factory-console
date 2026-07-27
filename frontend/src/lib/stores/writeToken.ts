import { get, writable, type Readable, type Writable } from 'svelte/store';
import type { ApiError } from '$lib/api/contracts';

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

// Prefixed so the key cannot collide with anything else this app stores. Storage is
// already partitioned per origin — and an origin includes the PORT, so another
// locally-served app on a different port shares nothing with us — so this is
// hygiene within our own namespace, not a cross-app boundary.
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

// The only handle that can mutate the token. Kept module-private so the storage
// mirror cannot be bypassed: a caller's `.set()` would update memory and leave
// sessionStorage holding the old value, which the next reload would hydrate back.
const tokenStore: Writable<string | null> = writable(readStoredToken());

/**
 * The current write token, or `null` when none is held. Hydrated once at import
 * from `sessionStorage`. Read-only by design — {@link setToken} / {@link clearToken}
 * are the only ways to change it, so the mirror always stays in step. (Same shape as
 * the sibling `live` store, which likewise exposes `Readable`s plus named mutators.)
 */
export const writeToken: Readable<string | null> = { subscribe: tokenStore.subscribe };

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
	tokenStore.set(trimmed);
	withSessionStorage<void>((storage) => storage.setItem(STORAGE_KEY, trimmed), undefined);
}

/**
 * Forget the token — for an explicit sign-out and for the 401 path, where the held
 * token is known bad. Removes the storage key outright rather than storing an empty
 * or `"null"` value, so the next load hydrates to `null`.
 */
export function clearToken(): void {
	tokenStore.set(null);
	withSessionStorage<void>((storage) => storage.removeItem(STORAGE_KEY), undefined);
}

/**
 * The envelope code the server answers a wrong or expired token with — source of
 * truth: `server/factory_console/api/write_token.py`. It lives here because it is
 * the one error code that invalidates what THIS module holds.
 */
const REJECTED_TOKEN_CODE = 'write_token_invalid';

/**
 * Run `action` with the held token, or call `onMissing` when none is held.
 *
 * Every write verb needs a token and every call site gates on it the same way:
 * raise its own prompt and resume the action from `WriteTokenPrompt`'s `onSaved`.
 * One helper so a change to the gate lands once instead of at each call site.
 */
export function withWriteToken(action: (token: string) => void, onMissing: () => void): void {
	const token = get(writeToken);
	if (!token) {
		onMissing();
		return;
	}
	action(token);
}

/**
 * Forget the held token when `error` is the server rejecting it, reporting whether
 * it did.
 *
 * Without this a wrong or expired token is a dead end: the prompts only mount
 * while NO token is held, so every retry would re-send the same rejected
 * credential and the user could never paste a new one — and a known-bad secret
 * would stay at rest in `sessionStorage`. Callers use the return value to re-raise
 * their prompt, which is the only failure here the user can fix in place.
 */
export function clearTokenIfRejected(error: ApiError): boolean {
	if (error.code !== REJECTED_TOKEN_CODE) return false;
	clearToken();
	return true;
}
