import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Fully mock the API barrel so neither `load` nor the write flows touch the
// network. The load imports the real `ApiError` CLASS from `$lib/api/errors` (NOT
// the barrel), so mocking the barrel still leaves `instanceof ApiError` working.
// The write wrappers are here because the page mounts `EditTicketModal`.
vi.mock('$lib/api', () => ({
	getTicket: vi.fn(),
	deleteTicket: vi.fn(),
	previewWrite: vi.fn(),
	updateTicket: vi.fn()
}));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));

import { goto } from '$app/navigation';
import { deleteTicket, getTicket } from '$lib/api';
import { clearToken, setToken, writeToken } from '$lib/stores/writeToken';
import { ApiError } from '$lib/api/errors';
import type { Ticket } from '$lib/api';
import type { PageData } from './$types';
import { load } from './+page';
import Page from './+page.svelte';

const getTicketMock = vi.mocked(getTicket);
const deleteTicketMock = vi.mocked(deleteTicket);
const gotoMock = vi.mocked(goto);

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

	it('de-duplicates repeated file paths instead of crashing the each key', () => {
		const dup = 'server/factory_console/app.py';
		const ticketWithDupFiles: Ticket = {
			...fullTicket,
			files: [dup, 'frontend/src/lib/api/client.ts', dup]
		};

		render(Page, { props: { data: foundData(ticketWithDupFiles) } });

		// A duplicate in the tolerant manifest must render once, not throw
		// `each_key_duplicate` on a non-unique keyed `{#each}`.
		expect(screen.getAllByText(dup)).toHaveLength(1);
		expect(screen.getByText('frontend/src/lib/api/client.ts')).toBeTruthy();
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

describe('ticket detail page (edit/delete gating)', () => {
	function editButton(): HTMLElement {
		return screen.getByRole('button', { name: 'Edit' });
	}
	function deleteButton(): HTMLElement {
		return screen.getByRole('button', { name: 'Delete' });
	}

	it.each(['in-flight', 'ready', 'merged'] as const)(
		'disables both actions and shows the gate banner for %s',
		(runState) => {
			render(Page, { props: { data: foundData({ ...fullTicket, runState }) } });

			expect(editButton().hasAttribute('disabled')).toBe(true);
			expect(deleteButton().hasAttribute('disabled')).toBe(true);
			expect(screen.getByRole('status').textContent).toContain('Read-only');
		}
	);

	it.each(['todo', 'unknown'] as const)(
		'enables both actions and hides the gate banner for %s',
		(runState) => {
			render(Page, { props: { data: foundData({ ...fullTicket, runState }) } });

			expect(editButton().hasAttribute('disabled')).toBe(false);
			expect(deleteButton().hasAttribute('disabled')).toBe(false);
			expect(screen.queryByRole('status')).toBeNull();
		}
	);

	it('opens the edit modal seeded with the ticket', async () => {
		render(Page, { props: { data: foundData({ ...fullTicket, runState: 'todo' }) } });

		expect(screen.queryByRole('button', { name: 'Save changes' })).toBeNull();
		await fireEvent.click(editButton());

		expect(screen.getByRole('button', { name: 'Save changes' })).toBeTruthy();
		expect((screen.getByLabelText('Ticket id') as HTMLInputElement).value).toBe('T31');
	});
});

describe('ticket detail page (delete)', () => {
	beforeEach(() => {
		clearToken();
		deleteTicketMock.mockReset();
		gotoMock.mockReset();
	});

	/** Click the dialog's own confirm — it carries the same label as the opener. */
	async function clickDialogDelete(): Promise<void> {
		const confirm = Array.from(screen.getByRole('dialog').querySelectorAll('button')).find(
			(button) => button.textContent?.trim() === 'Delete'
		);
		await fireEvent.click(confirm!);
	}

	async function confirmDelete(): Promise<void> {
		await fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
		await clickDialogDelete();
	}

	it('deletes behind the confirm dialog and returns to the list', async () => {
		setToken('tok-abc');
		deleteTicketMock.mockResolvedValue({
			applied: true,
			ticketId: 'T31',
			diff: { ticketId: 'T31' }
		});
		render(Page, { props: { data: foundData({ ...fullTicket, runState: 'todo' }) } });

		// Opening the dialog alone must not delete anything.
		await fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
		expect(deleteTicketMock).not.toHaveBeenCalled();

		await clickDialogDelete();

		await waitFor(() => expect(deleteTicketMock).toHaveBeenCalledWith('T31', 'tok-abc'));
		await waitFor(() => expect(gotoMock).toHaveBeenCalledWith('/'));
	});

	it('asks for a token first when none is held, then completes the delete', async () => {
		deleteTicketMock.mockResolvedValue({
			applied: true,
			ticketId: 'T31',
			diff: { ticketId: 'T31' }
		});
		render(Page, { props: { data: foundData({ ...fullTicket, runState: 'todo' }) } });

		await confirmDelete();

		expect(deleteTicketMock).not.toHaveBeenCalled();
		await fireEvent.input(screen.getByLabelText('Write token'), { target: { value: 'tok-xyz' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Save token' }));

		await waitFor(() => expect(deleteTicketMock).toHaveBeenCalledWith('T31', 'tok-xyz'));
	});

	it('shows a failed delete inline and stays on the ticket', async () => {
		setToken('tok-abc');
		deleteTicketMock.mockRejectedValue(
			new ApiError({ code: 'run_state_locked', message: 'Lane owns it.', status: 409 })
		);
		render(Page, { props: { data: foundData({ ...fullTicket, runState: 'todo' }) } });

		await confirmDelete();

		expect(await screen.findByText('run_state_locked')).toBeTruthy();
		expect(screen.getByText('Lane owns it.')).toBeTruthy();
		expect(gotoMock).not.toHaveBeenCalled();
	});

	// SvelteKit reuses this component when only `[id]` changes, and the page links
	// straight to other tickets. Without a reset, the prompt raised for one ticket
	// would still be on screen for the next — and resuming it would delete THAT
	// one, with no confirmation ever shown for it.
	it('clears a pending delete when the route swaps in another ticket', async () => {
		deleteTicketMock.mockResolvedValue({
			applied: true,
			ticketId: 'T31',
			diff: { ticketId: 'T31' }
		});
		const { rerender } = render(Page, {
			props: { data: foundData({ ...fullTicket, runState: 'todo' }) }
		});

		await confirmDelete();
		expect(screen.getByLabelText('Write token')).toBeTruthy();

		await rerender({ data: foundData({ ...fullTicket, id: 'T99', runState: 'todo' }) });

		// The prompt is gone, so nothing can resume against the ticket now shown.
		expect(screen.queryByLabelText('Write token')).toBeNull();
		expect(deleteTicketMock).not.toHaveBeenCalled();
	});

	it('clears a delete error when the route swaps in another ticket', async () => {
		setToken('tok-abc');
		deleteTicketMock.mockRejectedValue(
			new ApiError({ code: 'run_state_locked', message: 'Lane owns it.', status: 409 })
		);
		const { rerender } = render(Page, {
			props: { data: foundData({ ...fullTicket, runState: 'todo' }) }
		});

		await confirmDelete();
		expect(await screen.findByText('run_state_locked')).toBeTruthy();

		await rerender({ data: foundData({ ...fullTicket, id: 'T99', runState: 'todo' }) });

		expect(screen.queryByText('run_state_locked')).toBeNull();
	});

	// A rejected token is dropped so the prompt can come back; anything else is
	// left alone.
	it('drops a rejected token and re-prompts', async () => {
		setToken('tok-bad');
		deleteTicketMock.mockRejectedValue(
			new ApiError({ code: 'write_token_invalid', message: 'Bad token.', status: 401 })
		);
		render(Page, { props: { data: foundData({ ...fullTicket, runState: 'todo' }) } });

		await confirmDelete();

		expect(await screen.findByText('write_token_invalid')).toBeTruthy();
		expect(get(writeToken)).toBeNull();
		expect(screen.getByLabelText('Write token')).toBeTruthy();
	});
});

describe('ticket detail page (not found)', () => {
	it('renders the friendly not-found panel with a back-to-list link', () => {
		render(Page, { props: { data: notFoundData('nope') } });

		expect(screen.getByText(/Ticket "nope" not found/)).toBeTruthy();
		expect(screen.getByRole('link', { name: 'back to list' }).getAttribute('href')).toBe('/');
	});
});
