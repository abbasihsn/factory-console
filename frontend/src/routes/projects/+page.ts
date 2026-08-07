import type { PageLoad } from './$types';
import { listProjects } from '$lib/api';
// `throwBoundaryError` is imported DIRECTLY from its own module (not via the
// `$lib/api` barrel) so the load test can mock the barrel down to just
// `{ listProjects }` and still reach the real boundary policy — the pattern every
// other loader here uses.
import { throwBoundaryError } from '$lib/api/loadError';

// SPA-only load: `ssr`/`prerender` are already false globally in
// `routes/+layout.ts`, so no need to re-declare them here.
//
// Every registered row is rendered, degraded ones included — `listProjects` never
// drops one, and a row whose directory has moved or lost its `.factory/` is
// precisely what this management route exists to show (and to remove). So nothing
// is filtered here: only a genuine transport/server failure reaches the boundary.
//
// This is also the load `invalidateAll()` re-runs after a select or a remove: the
// page never patches its list locally, so the server stays the single source of
// truth for what is registered and which row is selected.
export const load: PageLoad = async () => {
	try {
		const projects = await listProjects();
		return { projects };
	} catch (err) {
		throwBoundaryError(err);
	}
};
