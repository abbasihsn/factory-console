import { render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Page from './+page.svelte';
import { load } from './+page';
import { ApiError, listTickets, type TicketSummary } from '$lib/api';

// `+page.svelte` renders FiltersBar, which imports `goto`; mock the navigation
// module so rendering needs no router. The `load` is unit-tested against a
// mocked `listTickets` so no backend is needed.
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$lib/api', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/api')>();
	return { ...actual, listTickets: vi.fn() };
});

const listTicketsMock = vi.mocked(listTickets);

const emptyFilters = { status: '', track: '', milestone: '', q: '' };

function ticket(id: string, title: string): TicketSummary {
	return {
		id,
		title,
		status: 'todo',
		track: 'frontend',
		milestone: 'MVP',
		runState: 'todo',
		depCount: 0,
		dependentCount: 0
	};
}

// `+page.svelte`'s `PageData` merges the root layout's `project` in, so the
// rendered `data` prop must carry it too (the page itself only reads items/filters).
const project = {
	rootPath: '/home/dev/factory-console',
	ticketsManifestPath: '/home/dev/factory-console/docs/planning/tickets.json',
	ticketsDir: '/home/dev/factory-console/docs/planning/tickets',
	roadmapPath: null,
	runStateDir: null,
	discoveredAt: '2026-07-22T00:00:00Z'
};

function pageData(items: TicketSummary[], filters = emptyFilters) {
	return { project, items, total: items.length, filters };
}

describe('tickets index page', () => {
	it('renders one row per ticket with a link to its detail route', () => {
		const items = [
			ticket('T30', 'Ticket list route'),
			ticket('T31', 'Ticket detail route'),
			ticket('T32', 'Dependency neighborhood')
		];
		const { container } = render(Page, { props: { data: pageData(items) } });

		const rowLinks = container.querySelectorAll('a[href^="/tickets/"]');
		expect(rowLinks).toHaveLength(3);
		expect(screen.getByText('Ticket list route')).toBeTruthy();
		expect(screen.getByRole('link', { name: 'T31' }).getAttribute('href')).toBe('/tickets/T31');
	});

	it('renders the empty state with a clear-filters link when there are no items', () => {
		render(Page, { props: { data: pageData([], { ...emptyFilters, q: 'zzz' }) } });

		expect(screen.getByText(/No tickets match/)).toBeTruthy();
		expect(screen.getByRole('link', { name: 'clear filters' }).getAttribute('href')).toBe('/');
	});
});

describe('tickets index load', () => {
	beforeEach(() => {
		listTicketsMock.mockReset();
	});

	it('reads the four filters from the URL and returns items + total + filters', async () => {
		const items = [ticket('T30', 'Ticket list route')];
		listTicketsMock.mockResolvedValue(items);

		const url = new URL('http://localhost/?status=done&track=frontend&q=route');
		const result = await load({ url } as never);

		expect(listTicketsMock).toHaveBeenCalledWith({
			status: 'done',
			track: 'frontend',
			milestone: '',
			q: 'route'
		});
		expect(result).toEqual({
			items,
			total: 1,
			filters: { status: 'done', track: 'frontend', milestone: '', q: 'route' }
		});
	});

	it('maps a network ApiError (status 0) to a 503 SvelteKit error', async () => {
		listTicketsMock.mockRejectedValue(
			new ApiError({ code: 'network_error', message: 'Could not reach the backend.', status: 0 })
		);

		await expect(load({ url: new URL('http://localhost/') } as never)).rejects.toMatchObject({
			status: 503,
			body: { code: 'network_error' }
		});
	});

	it('preserves a 4xx backend status on the SvelteKit error', async () => {
		listTicketsMock.mockRejectedValue(
			new ApiError({ code: 'invalid_filter', message: 'Bad filter.', status: 422 })
		);

		await expect(load({ url: new URL('http://localhost/') } as never)).rejects.toMatchObject({
			status: 422,
			body: { code: 'invalid_filter', message: 'Bad filter.' }
		});
	});
});
