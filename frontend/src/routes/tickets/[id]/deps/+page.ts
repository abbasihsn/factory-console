import type { PageLoad } from './$types';
import { getTicketDeps } from '$lib/api';
// Import the ApiError CLASS from its own module, NOT the barrel: this load's tests
// mock the barrel (`$lib/api`) to stub `getTicketDeps`, so reaching for `ApiError`
// through the barrel there would be `undefined` and break `instanceof`. The shared
// ApiError→boundary status policy lives in `throwBoundaryError` (in `$lib/api/loadError`,
// likewise imported directly so the barrel mock leaves it intact).
import { ApiError } from '$lib/api/errors';
import { throwBoundaryError } from '$lib/api/loadError';

// A 404 is the one failure the deps view handles inline: it returns
// `{ notFound: true }` so `+page.svelte` can render the friendly not-found panel
// (per the ticket's Context). Every OTHER failure is handed to the shared
// `throwBoundaryError`, which routes it to the SvelteKit `error()` boundary
// exactly like the index + detail loaders — a non-404 `ApiError` keeps its
// range-clamped backend status, and anything else becomes a 500 — so unexpected
// failures render in `+error.svelte` instead of falling into the component.
export const load: PageLoad = async ({ params }) => {
	try {
		const deps = await getTicketDeps(params.id);
		return { notFound: false as const, deps };
	} catch (err) {
		if (err instanceof ApiError && err.status === 404) {
			return { notFound: true as const, id: params.id };
		}
		throwBoundaryError(err);
	}
};
