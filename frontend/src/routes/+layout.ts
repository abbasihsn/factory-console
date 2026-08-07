import { error } from '@sveltejs/kit';
import type { LayoutLoad } from './$types';
import { listProjects, type RegisteredProjectOut } from '$lib/api';
import { normalizeError, type ApiError, type Project } from '$lib/api/contracts';

// SPA mode: no server-side rendering or prerendering. adapter-static emits a
// single index.html fallback that boots the client-side router.
export const ssr = false;
export const prerender = false;

const PROJECT_ENDPOINT = '/api/v1/project';

// Abort the project fetch if the backend accepts the socket but never responds,
// so a hung connection surfaces as the NETWORK_ERROR boundary instead of a
// permanently blank screen (nothing renders until this SPA-mode load settles).
const PROJECT_FETCH_TIMEOUT_MS = 10_000;

// Client-derived error for when the backend can't be reached at all (fetch
// rejects), as opposed to a backend error envelope from a non-OK response.
const NETWORK_ERROR: ApiError = {
	code: 'network_error',
	message: 'Could not reach the backend.',
	hint: 'Is the backend running?'
};

// A 2xx whose body isn't the expected JSON (e.g. an HTML proxy page at 200) would
// otherwise throw a raw SyntaxError out of this load, blanking the whole shell
// instead of rendering the error boundary. Route it to the boundary like any
// other unreachable-project failure.
const INVALID_RESPONSE: ApiError = {
	code: 'invalid_response',
	message: 'The backend returned an unreadable response.'
};

// Fetch the resolved project once so every route can show it in the top bar,
// alongside the registry rows the switcher offers. Both reads are issued together
// (`Promise.all`) because neither needs the other's answer, so the shell waits on
// the slower one rather than on their sum.
//
// Only the project read is fatal (see `loadProject`): the registry degrades to an
// empty list, and the switcher renders nothing for it, leaving today's
// single-project shell exactly as it was.
export const load: LayoutLoad = async ({ fetch }) => {
	const [project, projects] = await Promise.all([loadProject(fetch), loadRegistry()]);
	return { project, projects, selectedId: selectedIdOf(projects) };
};

/**
 * Which row the switcher shows as current.
 *
 * Read off the rows rather than fetched from `GET /projects/current`: the server
 * flattens `selected` onto every row from that same selection state, so the list
 * response already carries the answer and a second round-trip could only
 * disagree with it. `null` covers both "no registry" (the degraded empty list)
 * and a selection the registry cannot name.
 */
function selectedIdOf(projects: readonly RegisteredProjectOut[]): string | null {
	return projects.find((project) => project.selected)?.id ?? null;
}

/**
 * The registry rows, or `[]` when the registry cannot be read.
 *
 * **A registry read that fails must not blank the shell.** The console served one
 * project long before it could switch between them, and it still can: an
 * unreachable, unreadable or not-yet-present registry costs the user the
 * dropdown, not the app. Every other failure in this load is fatal precisely
 * because nothing can render without a project — that is not true here.
 */
async function loadRegistry(): Promise<RegisteredProjectOut[]> {
	try {
		return await listProjects();
	} catch {
		return [];
	}
}

// Failures become SvelteKit errors so `page.error` carries a normalized
// `ApiError` that `+error.svelte` renders.
async function loadProject(fetch: typeof globalThis.fetch): Promise<Project> {
	let response: Response;
	try {
		response = await fetch(PROJECT_ENDPOINT, {
			signal: AbortSignal.timeout(PROJECT_FETCH_TIMEOUT_MS)
		});
	} catch {
		// Covers connection refused AND a timeout (AbortSignal.timeout throws a
		// TimeoutError DOMException) — both mean "couldn't reach the backend".
		throw error(503, NETWORK_ERROR);
	}

	if (!response.ok) {
		const body: unknown = await response.json().catch(() => null);
		throw error(response.status, normalizeError(body));
	}

	try {
		return (await response.json()) as Project;
	} catch {
		throw error(503, INVALID_RESPONSE);
	}
}
