import type { PageLoad } from './$types';
import { getRoadmap } from '$lib/api';
// `throwBoundaryError` is imported DIRECTLY from its own module (not via the
// `$lib/api` barrel) so the load test can mock the barrel down to just
// `{ getRoadmap }` and still reach the real boundary policy — mirroring the graph
// loader. Every unexpected failure is handed straight to the shared boundary.
import { throwBoundaryError } from '$lib/api/loadError';

// SPA-only load: `ssr`/`prerender` are already false globally in
// `routes/+layout.ts`, so no need to re-declare them here.
export const load: PageLoad = async () => {
	try {
		const roadmap = await getRoadmap();
		// `getRoadmap()` returns a union: the present branch carries the document
		// fields and NO `present` key, while the absent branch sets `present: false`.
		// Discriminate on that key's presence.
		if ('present' in roadmap) {
			return { present: false as const };
		}
		return { present: true as const, roadmap };
	} catch (err) {
		throwBoundaryError(err);
	}
};
