import type { PageLoad } from './$types';
import { listTickets, type Filters } from '$lib/api';
// The ApiError→boundary status policy is shared with the detail + deps loaders
// via `throwBoundaryError` in `$lib/api/loadError` — imported directly, NOT
// through the mocked `$lib/api` barrel, so the load's tests keep working.
import { throwBoundaryError } from '$lib/api/loadError';

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
		throwBoundaryError(err);
	}
};
