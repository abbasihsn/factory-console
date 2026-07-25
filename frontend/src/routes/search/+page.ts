import type { PageLoad } from './$types';
import { searchTickets, type SearchHit } from '$lib/api';
// The ApiError→boundary status policy is shared with the index + graph + deps
// loaders via `throwBoundaryError` in `$lib/api/loadError` — imported directly,
// NOT through the mocked `$lib/api` barrel, so the load's tests keep working.
import { throwBoundaryError } from '$lib/api/loadError';

// The URL is the single source of truth for the search term, mirroring the index
// loader. An empty (or whitespace-only) `q` short-circuits: return no results
// WITHOUT a round-trip, so /search with a blank box renders its empty state.
// (`ssr`/`prerender` are set once on the root layout, not here.)
export const load: PageLoad = async ({ url }) => {
	const q = url.searchParams.get('q') ?? '';

	if (q.trim() === '') {
		return { q, results: [] as SearchHit[] };
	}

	try {
		const results = await searchTickets({ q });
		return { q, results };
	} catch (err) {
		throwBoundaryError(err);
	}
};
