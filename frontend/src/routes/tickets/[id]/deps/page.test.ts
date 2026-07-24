import { render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Fully mock the API barrel so `load` never touches the network. The load imports
// the real `ApiError` CLASS from `$lib/api/errors` (NOT the barrel), so mocking the
// barrel down to just `{ getTicketDeps }` still leaves `instanceof ApiError` working.
vi.mock('$lib/api', () => ({ getTicketDeps: vi.fn() }));

import { getTicketDeps } from '$lib/api';
import { ApiError } from '$lib/api/errors';
import type { DepNeighborhood, TicketSummary } from '$lib/api';
import type { PageData } from './$types';
import { load } from './+page';
import Page from './+page.svelte';

const getTicketDepsMock = vi.mocked(getTicketDeps);

// `+page.svelte`'s `PageData` merges the root layout's `project`, so the rendered
// `data` prop must carry it too (the page itself only reads the deps / id).
const project = {
	rootPath: '/home/dev/factory-console',
	ticketsManifestPath: '/home/dev/factory-console/docs/planning/tickets.json',
	ticketsDir: '/home/dev/factory-console/docs/planning/tickets',
	roadmapPath: null,
	runStateDir: null,
	discoveredAt: '2026-07-22T00:00:00Z'
};

// A minimal-but-complete `TicketSummary` builder so each fixture row only spells out
// what the assertion cares about (its id / title).
function summary(overrides: Partial<TicketSummary> & Pick<TicketSummary, 'id'>): TicketSummary {
	return {
		title: `Title for ${overrides.id}`,
		status: 'in_progress',
		track: 'frontend',
		milestone: 'MVP',
		runState: 'in-flight',
		depCount: 0,
		dependentCount: 0,
		...overrides
	};
}

// Non-empty in all three lists so every rendered branch is exercised.
const neighborhood: DepNeighborhood = {
	ticket: summary({ id: 'T32', title: 'Dep neighborhood route', depCount: 2, dependentCount: 1 }),
	directDeps: [
		summary({ id: 'T31', title: 'Ticket detail route' }),
		summary({ id: 'T29', title: 'Markdown body component' })
	],
	directDependents: [summary({ id: 'T40', title: 'Downstream consumer' })],
	unresolvedDeps: ['GHOST-1', 'GHOST-2']
};

// All three lists empty so the muted "None" placeholder renders three times.
const emptyNeighborhood: DepNeighborhood = {
	ticket: summary({ id: 'T99', title: 'Lonely ticket' }),
	directDeps: [],
	directDependents: [],
	unresolvedDeps: []
};

// The three dep arrays are OPTIONAL in the generated schema, so the backend may
// omit them entirely (not just send `[]`). This fixture leaves them out to
// exercise the `?? []` guards in `+page.svelte` — without them, `.length` on an
// absent field would throw at render.
const sparseNeighborhood: DepNeighborhood = {
	ticket: summary({ id: 'T98', title: 'Fieldless ticket' })
};

function foundData(deps: DepNeighborhood): PageData {
	return { project, notFound: false, deps };
}

function notFoundData(id: string): PageData {
	return { project, notFound: true, id };
}

describe('dep neighborhood load', () => {
	beforeEach(() => {
		getTicketDepsMock.mockReset();
	});

	it('returns the neighborhood wrapped as found on success', async () => {
		getTicketDepsMock.mockResolvedValue(neighborhood);

		const result = await load({ params: { id: 'T32' } } as never);

		expect(getTicketDepsMock).toHaveBeenCalledWith('T32');
		expect(result).toEqual({ notFound: false, deps: neighborhood });
	});

	it('returns a not-found result (not a throw) on a 404 ApiError', async () => {
		getTicketDepsMock.mockRejectedValue(
			new ApiError({ code: 'ticket_not_found', message: 'No such ticket.', status: 404 })
		);

		await expect(load({ params: { id: 'nope' } } as never)).resolves.toEqual({
			notFound: true,
			id: 'nope'
		});
	});

	it('converts a non-404 ApiError to a thrown SvelteKit error preserving status + code', async () => {
		getTicketDepsMock.mockRejectedValue(
			new ApiError({ code: 'internal_error', message: 'Boom.', status: 500 })
		);

		await expect(load({ params: { id: 'T32' } } as never)).rejects.toMatchObject({
			status: 500,
			body: { code: 'internal_error', message: 'Boom.' }
		});
	});

	it('maps a network ApiError (status 0) to a 503', async () => {
		getTicketDepsMock.mockRejectedValue(
			new ApiError({ code: 'network_error', message: 'Could not reach the backend.', status: 0 })
		);

		await expect(load({ params: { id: 'T32' } } as never)).rejects.toMatchObject({
			status: 503,
			body: { code: 'network_error' }
		});
	});
});

describe('dep neighborhood page (found)', () => {
	it('renders the header and a back-link to the ticket detail route', () => {
		render(Page, { props: { data: foundData(neighborhood) } });

		expect(screen.getByRole('heading', { level: 1, name: 'Deps for T32' })).toBeTruthy();
		expect(screen.getByRole('link', { name: /back to ticket/ }).getAttribute('href')).toBe(
			'/tickets/T32'
		);
	});

	it('renders a mini-row anchor pointing at each direct dependency', () => {
		render(Page, { props: { data: foundData(neighborhood) } });

		for (const dep of neighborhood.directDeps ?? []) {
			const link = screen.getByRole('link', { name: dep.id });
			expect(link.getAttribute('href')).toBe(`/tickets/${dep.id}`);
		}
	});

	it('renders a mini-row anchor pointing at each direct dependent', () => {
		render(Page, { props: { data: foundData(neighborhood) } });

		for (const dependent of neighborhood.directDependents ?? []) {
			const link = screen.getByRole('link', { name: dependent.id });
			expect(link.getAttribute('href')).toBe(`/tickets/${dependent.id}`);
		}
	});

	it('renders unresolved deps as plain text, never as links', () => {
		render(Page, { props: { data: foundData(neighborhood) } });

		for (const depId of neighborhood.unresolvedDeps ?? []) {
			expect(screen.getByText(depId)).toBeTruthy();
			expect(screen.queryByRole('link', { name: depId })).toBeNull();
		}
	});

	it('renders a muted "None" for each of the three empty sections', () => {
		render(Page, { props: { data: foundData(emptyNeighborhood) } });

		expect(screen.getAllByText('None')).toHaveLength(3);
	});

	it('renders three "None" placeholders when the optional dep arrays are absent', () => {
		render(Page, { props: { data: foundData(sparseNeighborhood) } });

		expect(screen.getAllByText('None')).toHaveLength(3);
	});
});

describe('dep neighborhood page (not found)', () => {
	it('renders the friendly not-found panel with the id and a back-to-list link', () => {
		render(Page, { props: { data: notFoundData('nope') } });

		expect(screen.getByText(/Ticket "nope" not found/)).toBeTruthy();
		expect(screen.getByRole('link', { name: 'back to list' }).getAttribute('href')).toBe('/');
	});
});
