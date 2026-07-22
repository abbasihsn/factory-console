import { render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Fully mock the API barrel so `load` never touches the network. The load imports
// the real `ApiError` CLASS from `$lib/api/errors` (NOT the barrel), so mocking the
// barrel down to just `{ getTicket }` still leaves `instanceof ApiError` working.
vi.mock('$lib/api', () => ({ getTicket: vi.fn() }));

import { getTicket } from '$lib/api';
import { ApiError } from '$lib/api/errors';
import type { Ticket } from '$lib/api';
import type { PageData } from './$types';
import { load } from './+page';
import Page from './+page.svelte';

const getTicketMock = vi.mocked(getTicket);

// `+page.svelte`'s `PageData` merges the root layout's `project`, so the rendered
// `data` prop must carry it too (the page itself only reads the ticket / id).
const project = {
	rootPath: '/home/dev/factory-console',
	ticketsManifestPath: '/home/dev/factory-console/docs/planning/tickets.json',
	ticketsDir: '/home/dev/factory-console/docs/planning/tickets',
	roadmapPath: null,
	runStateDir: null,
	discoveredAt: '2026-07-22T00:00:00Z'
};

// A fully-populated ticket so every field-driven branch of the view renders.
const fullTicket: Ticket = {
	id: 'T31',
	title: 'Ticket detail route',
	status: 'in_progress',
	track: 'frontend',
	milestone: 'MVP',
	runState: 'in-flight',
	dependsOn: ['T30', 'T29', 'T22'],
	provides: ['Detail route rendering'],
	files: [
		'frontend/src/routes/tickets/[id]/+page.svelte',
		'frontend/src/lib/components/ChipList.svelte'
	],
	filePath: '/docs/planning/tickets/mvp/T31-ticket-detail-route.md',
	bodyMarkdown: '## Rendered heading\n\nRendered body paragraph.',
	bodyHtml: '<h2>Rendered heading</h2>\n<p>Rendered body paragraph.</p>',
	raw: {}
};

function foundData(ticket: Ticket): PageData {
	return { project, notFound: false, ticket };
}

function notFoundData(id: string): PageData {
	return { project, notFound: true, id };
}

describe('ticket detail load', () => {
	beforeEach(() => {
		getTicketMock.mockReset();
	});

	it('returns the ticket wrapped as found on success', async () => {
		getTicketMock.mockResolvedValue(fullTicket);

		const result = await load({ params: { id: 'T31' } } as never);

		expect(getTicketMock).toHaveBeenCalledWith('T31');
		expect(result).toEqual({ notFound: false, ticket: fullTicket });
	});

	it('returns a not-found result (not a throw) on a 404 ApiError', async () => {
		getTicketMock.mockRejectedValue(
			new ApiError({ code: 'ticket_not_found', message: 'No such ticket.', status: 404 })
		);

		await expect(load({ params: { id: 'nope' } } as never)).resolves.toEqual({
			notFound: true,
			id: 'nope'
		});
	});

	it('converts a non-404 ApiError to a thrown SvelteKit error preserving status + code', async () => {
		getTicketMock.mockRejectedValue(
			new ApiError({ code: 'internal_error', message: 'Boom.', status: 500 })
		);

		await expect(load({ params: { id: 'T31' } } as never)).rejects.toMatchObject({
			status: 500,
			body: { code: 'internal_error', message: 'Boom.' }
		});
	});

	it('maps a network ApiError (status 0) to a 503', async () => {
		getTicketMock.mockRejectedValue(
			new ApiError({ code: 'network_error', message: 'Could not reach the backend.', status: 0 })
		);

		await expect(load({ params: { id: 'T31' } } as never)).rejects.toMatchObject({
			status: 503,
			body: { code: 'network_error' }
		});
	});
});

describe('ticket detail page (found)', () => {
	it('renders the title, both badges, and the track/milestone chips', () => {
		render(Page, { props: { data: foundData(fullTicket) } });

		expect(screen.getByRole('heading', { level: 1, name: 'Ticket detail route' })).toBeTruthy();
		// StatusBadge renders the raw status; RunStateBadge renders a humanized label.
		expect(screen.getByText('in_progress')).toBeTruthy();
		expect(screen.getByText('In flight')).toBeTruthy();
		expect(screen.getByText('frontend')).toBeTruthy();
		expect(screen.getByText('MVP')).toBeTruthy();
	});

	it('renders each dependsOn chip as an anchor linking to that ticket', () => {
		render(Page, { props: { data: foundData(fullTicket) } });

		for (const depId of ['T30', 'T29', 'T22']) {
			const link = screen.getByRole('link', { name: depId });
			expect(link.tagName).toBe('A');
			expect(link.getAttribute('href')).toBe(`/tickets/${depId}`);
		}
	});

	it('renders provides chips as plain (non-anchor) text', () => {
		render(Page, { props: { data: foundData(fullTicket) } });

		expect(screen.getByText('Detail route rendering')).toBeTruthy();
		expect(screen.queryByRole('link', { name: 'Detail route rendering' })).toBeNull();
	});

	it('renders file paths as plain text, never as links', () => {
		const { container } = render(Page, { props: { data: foundData(fullTicket) } });

		// No file path becomes a `file://` (or any) link.
		expect(container.querySelector('a[href^="file:"]')).toBeNull();
		for (const file of fullTicket.files ?? []) {
			expect(screen.getByText(file)).toBeTruthy();
			expect(screen.queryByRole('link', { name: file })).toBeNull();
		}
	});

	it('links to the dependency neighborhood route', () => {
		render(Page, { props: { data: foundData(fullTicket) } });

		expect(screen.getByRole('link', { name: /View dep neighborhood/ }).getAttribute('href')).toBe(
			'/tickets/T31/deps'
		);
	});

	it('passes bodyHtml verbatim to MarkdownBody', () => {
		const { container } = render(Page, { props: { data: foundData(fullTicket) } });

		// The server-sanitized HTML is injected as-is (the single `@html` boundary).
		expect(container.innerHTML).toContain('<h2>Rendered heading</h2>');
		expect(screen.getByText('Rendered body paragraph.')).toBeTruthy();
	});
});

describe('ticket detail page (not found)', () => {
	it('renders the friendly not-found panel with a back-to-list link', () => {
		render(Page, { props: { data: notFoundData('nope') } });

		expect(screen.getByText(/Ticket "nope" not found/)).toBeTruthy();
		expect(screen.getByRole('link', { name: 'back to list' }).getAttribute('href')).toBe('/');
	});
});
