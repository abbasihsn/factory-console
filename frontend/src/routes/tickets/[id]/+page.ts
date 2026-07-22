import { error } from '@sveltejs/kit';
import type { PageLoad } from './$types';
import { getTicket } from '$lib/api';
// Import the ApiError CLASS from its own module, NOT the barrel: this load's tests
// mock the barrel (`$lib/api`) to stub `getTicket`, so reaching for `ApiError`
// through the barrel there would be `undefined` and break `instanceof`.
import { ApiError } from '$lib/api/errors';
import { normalizeError } from '$lib/api/contracts';

// A network-failure `ApiError` carries `status: 0`, which SvelteKit's `error()`
// rejects (it only accepts 400–599); map anything outside that range to 503 so
// the boundary still renders — the same mapping the index + layout loaders use.
const SERVICE_UNAVAILABLE = 503;
const INTERNAL_ERROR = 500;

// A 404 is the one failure the detail view handles inline: it returns
// `{ notFound: true }` so `+page.svelte` can render the friendly not-found panel
// (per the ticket's Context). Every OTHER failure is converted to the SvelteKit
// `error()` boundary exactly like the index `+page.ts` — a non-404 `ApiError`
// keeps its (range-clamped) backend status, and anything else becomes a 500 —
// so unexpected failures route to `+error.svelte` instead of into the component.
export const load: PageLoad = async ({ params }) => {
	try {
		const ticket = await getTicket(params.id);
		return { notFound: false as const, ticket };
	} catch (err) {
		if (err instanceof ApiError) {
			if (err.status === 404) {
				return { notFound: true as const, id: params.id };
			}
			const status = err.status >= 400 && err.status <= 599 ? err.status : SERVICE_UNAVAILABLE;
			throw error(status, normalizeError(err));
		}
		// Anything that isn't a transport error is unexpected → a generic 500.
		throw error(INTERNAL_ERROR, normalizeError(err));
	}
};
