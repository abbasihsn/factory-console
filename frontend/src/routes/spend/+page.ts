import type { PageLoad } from './$types';
import { getSpend } from '$lib/api';
// `throwBoundaryError` is imported DIRECTLY from its own module (not via the
// `$lib/api` barrel) so the load test can mock the barrel down to just
// `{ getSpend }` and still reach the real boundary policy — mirroring the roadmap
// and graph loaders.
import { throwBoundaryError } from '$lib/api/loadError';

// SPA-only load: `ssr`/`prerender` are already false globally in
// `routes/+layout.ts`, so no need to re-declare them here.
//
// A missing ledger is NOT an error and is not handled here: the endpoint answers
// it with a normal 200 body carrying `source.found: false`, and the page renders
// the explanation. Only a genuine transport/server failure reaches the boundary.
export const load: PageLoad = async () => {
	try {
		const spend = await getSpend();
		return { spend };
	} catch (err) {
		throwBoundaryError(err);
	}
};
