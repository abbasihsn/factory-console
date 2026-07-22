import { error } from '@sveltejs/kit';
import type { LayoutLoad } from './$types';
import { normalizeError, type ApiError, type Project } from '$lib/api/contracts';

// SPA mode: no server-side rendering or prerendering. adapter-static emits a
// single index.html fallback that boots the client-side router.
export const ssr = false;
export const prerender = false;

const PROJECT_ENDPOINT = '/api/v1/project';

// Client-derived error for when the backend can't be reached at all (fetch
// rejects), as opposed to a backend error envelope from a non-OK response.
const NETWORK_ERROR: ApiError = {
	code: 'network_error',
	message: 'Could not reach the backend.',
	hint: 'Is the backend running?'
};

// Fetch the resolved project once so every route can show it in the top bar.
// Failures become SvelteKit errors so `page.error` carries a normalized
// `ApiError` that `+error.svelte` renders.
export const load: LayoutLoad = async ({ fetch }) => {
	let response: Response;
	try {
		response = await fetch(PROJECT_ENDPOINT);
	} catch {
		throw error(503, NETWORK_ERROR);
	}

	if (!response.ok) {
		const body: unknown = await response.json().catch(() => null);
		throw error(response.status, normalizeError(body));
	}

	const project = (await response.json()) as Project;
	return { project };
};
