import type { PageLoad } from './$types';
import { getGraph } from '$lib/api';
// `throwBoundaryError` is imported DIRECTLY from its own module (not via the
// `$lib/api` barrel) so the load test can mock the barrel down to just
// `{ getGraph }` and still reach the real boundary policy — mirroring the deps
// loader. Unlike deps, the graph view has no inline-handled status (no 404
// special case): every failure is handed straight to the shared boundary.
import { throwBoundaryError } from '$lib/api/loadError';

// SPA-only load: `ssr`/`prerender` are already false globally in
// `routes/+layout.ts`, so no need to re-declare them here.
export const load: PageLoad = async () => {
	try {
		const graph = await getGraph();
		return { graph };
	} catch (err) {
		throwBoundaryError(err);
	}
};
