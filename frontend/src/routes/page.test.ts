import { render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Fully mock the API barrel so `load` never touches the network. The load imports
// the real `ApiError` CLASS from `$lib/api/errors` (NOT the barrel), so mocking the
// barrel down to just `{ listTickets }` still leaves `instanceof ApiError` working.
vi.mock('$lib/api', () => ({ listTickets: vi.fn() }));
// Belt-and-braces: rendering the route imports `goto`; stub it so no real router
// is touched under jsdom.
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

import { listTickets } from '$lib/api';
import { ApiError } from '$lib/api/errors';
import type { TicketSummary } from '$lib/api';
import { load } from './+page';
import Page from './+page.svelte';

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

// `+page.svelte`'s `PageData` merges the root layout's `project`, so the rendered
// `data` prop must carry it too (the page itself only reads items/filters).
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
	it('renders one row per ticket, each linking to its detail route', () => {
		const items = [
			ticket('T30', 'Ticket list route'),
			ticket('T31', 'Ticket detail route'),
			ticket('T32', 'Dependency neighborhood')
		];
		const { container } = render(Page, { props: { data: pageData(items) } });

		expect(container.querySelectorAll('a[href^="/tickets/"]')).toHaveLength(3);
		expect(screen.getByRole('link', { name: 'T31' }).getAttribute('href')).toBe('/tickets/T31');
	});

	it('renders the empty state with a clear-filters reset link when there are no items', () => {
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

	it('preserves a 4xx backend status and code on the thrown SvelteKit error', async () => {
		listTicketsMock.mockRejectedValue(
			new ApiError({ code: 'ticket_not_found', message: 'No such ticket.', status: 404 })
		);

		await expect(load({ url: new URL('http://localhost/') } as never)).rejects.toMatchObject({
			status: 404,
			body: { code: 'ticket_not_found', message: 'No such ticket.' }
		});
	});

	it('maps a network ApiError (status 0) to a 503', async () => {
		listTicketsMock.mockRejectedValue(
			new ApiError({ code: 'network_error', message: 'Could not reach the backend.', status: 0 })
		);

		await expect(load({ url: new URL('http://localhost/') } as never)).rejects.toMatchObject({
			status: 503,
			body: { code: 'network_error' }
		});
	});
});
