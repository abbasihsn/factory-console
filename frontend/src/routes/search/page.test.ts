import { render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Fully mock the API barrel so `load` never touches the network. The load imports
// the real `throwBoundaryError` from `$lib/api/loadError` (NOT the barrel) and the
// real `ApiError` CLASS from `$lib/api/errors`, so mocking the barrel down to just
// `{ searchTickets }` still leaves the boundary policy + `instanceof ApiError`
// intact. TicketMiniRow is presentational and `$app`-free, so the page needs no
// router stub.
vi.mock('$lib/api', () => ({ searchTickets: vi.fn() }));

import { searchTickets } from '$lib/api';
import { ApiError } from '$lib/api/errors';
import type { SearchHit } from '$lib/api';
import type { PageData } from './$types';
import { load } from './+page';
import Page from './+page.svelte';

const searchTicketsMock = vi.mocked(searchTickets);

// `+page.svelte`'s `PageData` merges the root layout's `project`, so the rendered
// `data` prop must carry it too (the page itself only reads `data.q`/`data.results`).
const project = {
	rootPath: '/home/dev/factory-console',
	ticketsManifestPath: '/home/dev/factory-console/docs/planning/tickets.json',
	ticketsDir: '/home/dev/factory-console/docs/planning/tickets',
	roadmapPath: null,
	runStateDir: null,
	discoveredAt: '2026-07-22T00:00:00Z'
};

const hit: SearchHit = {
	ticket: {
		id: 'T41',
		title: 'Full-text search endpoint',
		status: 'done',
		track: 'backend',
		milestone: 'v1',
		runState: 'todo',
		depCount: 0,
		dependentCount: 1
	},
	score: 4.2,
	matchedFields: ['id', 'title']
};

function urlFor(q: string | null): { url: URL } {
	const url = new URL('http://localhost/search');
	if (q !== null) url.searchParams.set('q', q);
	return { url };
}

function pageData(q: string, results: SearchHit[]): PageData {
	return { project, q, results };
}

describe('search load', () => {
	beforeEach(() => {
		searchTicketsMock.mockReset();
	});

	it('returns the ranked hits on success', async () => {
		searchTicketsMock.mockResolvedValue([hit]);

		const result = await load(urlFor('auth') as never);

		expect(searchTicketsMock).toHaveBeenCalledTimes(1);
		expect(searchTicketsMock).toHaveBeenCalledWith({ q: 'auth' });
		expect(result).toEqual({ q: 'auth', results: [hit] });
	});

	it('short-circuits an empty q without calling the API', async () => {
		const result = await load(urlFor('') as never);

		expect(searchTicketsMock).not.toHaveBeenCalled();
		expect(result).toEqual({ q: '', results: [] });
	});

	it('short-circuits a whitespace-only q without calling the API', async () => {
		const result = await load(urlFor('   ') as never);

		expect(searchTicketsMock).not.toHaveBeenCalled();
		expect(result).toEqual({ q: '   ', results: [] });
	});

	it('converts an ApiError to a thrown SvelteKit boundary error preserving status + code', async () => {
		searchTicketsMock.mockRejectedValue(
			new ApiError({ code: 'internal_error', message: 'Boom.', status: 500 })
		);

		await expect(load(urlFor('auth') as never)).rejects.toMatchObject({
			status: 500,
			body: { code: 'internal_error', message: 'Boom.' }
		});
	});
});

describe('search page', () => {
	it('renders each hit via TicketMiniRow plus its matched fields', () => {
		render(Page, { props: { data: pageData('auth', [hit]) } });

		expect(screen.getByRole('heading', { level: 1, name: 'Search' })).toBeTruthy();
		expect(screen.getByRole('link', { name: 'T41' }).getAttribute('href')).toBe('/tickets/T41');
		expect(screen.getByText('Full-text search endpoint')).toBeTruthy();
		expect(screen.getByText('matched: id, title')).toBeTruthy();
	});

	it('renders the no-results state when the query matched nothing', () => {
		render(Page, { props: { data: pageData('zzz', []) } });

		expect(screen.getByText('No tickets match “zzz”.')).toBeTruthy();
	});

	it('renders the prompt state when the query is empty', () => {
		render(Page, { props: { data: pageData('', []) } });

		expect(screen.getByText('Type a term in the search box to find tickets.')).toBeTruthy();
	});
});
