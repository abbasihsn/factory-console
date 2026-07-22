import { error } from '@sveltejs/kit';
import type { PageLoad } from './$types';
import { listTickets, type Filters } from '$lib/api';
// Import the ApiError CLASS from its own module, NOT the barrel: the load's tests
// mock the barrel (`$lib/api`) to stub `listTickets`, so reaching for `ApiError`
// through the barrel there would be `undefined` and break `instanceof`.
import { ApiError } from '$lib/api/errors';
import { normalizeError } from '$lib/api/contracts';

// A network-failure `ApiError` carries `status: 0`, which SvelteKit's `error()`
// rejects (it only accepts 400–599); map anything outside that range to 503 so
// the boundary still renders — the same mapping the layout loader uses.
const SERVICE_UNAVAILABLE = 503;
const INTERNAL_ERROR = 500;

// The URL is the single source of truth for filter + search state: read the four
// filter params (defaulting each missing one to '') and let the backend do the
// filtering. The client wrapper drops empty-string params, so `filters` passes
// straight through. (`ssr`/`prerender` are set once on the root layout, not here.)
export const load: PageLoad = async ({ url }) => {
	const filters: Filters = {
		status: url.searchParams.get('status') ?? '',
		track: url.searchParams.get('track') ?? '',
		milestone: url.searchParams.get('milestone') ?? '',
		q: url.searchParams.get('q') ?? ''
	};

	try {
		const items = await listTickets(filters);
		// `total === items.length` in the MVP: the client wrapper drops the envelope
		// `total` and there is no pagination, so the row count is the total.
		return { items, total: items.length, filters };
	} catch (err) {
		if (err instanceof ApiError) {
			const status = err.status >= 400 && err.status <= 599 ? err.status : SERVICE_UNAVAILABLE;
			throw error(status, normalizeError(err));
		}
		// Anything that isn't a transport error is unexpected → a generic 500.
		throw error(INTERNAL_ERROR, normalizeError(err));
	}
};
