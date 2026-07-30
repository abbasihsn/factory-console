import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Fully mock the API barrel so nothing touches the network. The create route only
// reaches the API through `previewWrite` (dry-run) and `createTicket` (apply); the
// page normalizes errors through `$lib/api/contracts`, which is left real.
vi.mock('$lib/api', () => ({
	previewWrite: vi.fn(),
	createTicket: vi.fn()
}));

// The successful create leaves for the new ticket, so navigation is stubbed like
// every other route test that owns a `goto`.
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

import { goto } from '$app/navigation';
import { createTicket, previewWrite } from '$lib/api';
import { ApiError } from '$lib/api/errors';
import type { WriteResult } from '$lib/api';
import { clearToken, setToken, writeToken } from '$lib/stores/writeToken';
import type { PageData } from './$types';
import { load } from './+page';
import Page from './+page.svelte';

const previewWriteMock = vi.mocked(previewWrite);
const createTicketMock = vi.mocked(createTicket);
const gotoMock = vi.mocked(goto);

const TOKEN = 'test-write-token';

// `+page.svelte`'s `PageData` merges the root layout's `project`, so the rendered
// `data` prop must carry it too (the page itself only reads `initial`).
const project = {
	rootPath: '/home/dev/factory-console',
	ticketsManifestPath: '/home/dev/factory-console/docs/planning/tickets.json',
	ticketsDir: '/home/dev/factory-console/docs/planning/tickets',
	roadmapPath: null,
	runStateDir: null,
	discoveredAt: '2026-07-22T00:00:00Z'
};

const emptyInitial = { id: '', title: '', dependsOn: '', provides: '', files: '', body: '' };

function pageData(): PageData {
	return { project, initial: emptyInitial };
}

// The dry-run envelope the server answers a preview with: nothing written yet.
const previewResult: WriteResult = {
	applied: false,
	ticketId: 'T99',
	diff: {
		ticketId: 'T99',
		files: [
			{
				path: 'docs/planning/tickets/T99-new-ticket.md',
				changeKind: 'create',
				diff: '@@ -0,0 +1 @@\n+new\n'
			}
		]
	},
	ticket: null
};

describe('create ticket load', () => {
	it('returns empty initial form values with no data fetch', async () => {
		const result = await load({} as never);
		expect(result).toEqual({ initial: emptyInitial });
	});
});

// Fill the two required fields and submit the create form.
async function submitValidForm(id = 'T99', title = 'A brand new ticket'): Promise<void> {
	await fireEvent.input(screen.getByLabelText('Ticket id'), { target: { value: id } });
	await fireEvent.input(screen.getByLabelText('Title'), { target: { value: title } });
	await fireEvent.click(screen.getByRole('button', { name: 'Create ticket' }));
}

describe('create ticket route', () => {
	beforeEach(() => {
		previewWriteMock.mockReset();
		createTicketMock.mockReset();
		gotoMock.mockReset();
		clearToken();
	});

	it('previews the create, then applies and navigates to the new ticket on confirm', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(previewResult);
		createTicketMock.mockResolvedValue({
			applied: true,
			ticketId: 'T99',
			diff: { ticketId: 'T99' },
			ticket: null
		});
		render(Page, { props: { data: pageData() } });

		await submitValidForm();

		const expectedBody = {
			id: 'T99',
			title: 'A brand new ticket',
			dependsOn: [],
			provides: '',
			files: [],
			bodyMarkdown: ''
		};
		await waitFor(() =>
			expect(previewWriteMock).toHaveBeenCalledWith({ verb: 'create', body: expectedBody }, TOKEN)
		);

		// The dry-run wrote nothing yet.
		expect(createTicketMock).not.toHaveBeenCalled();

		await fireEvent.click(await screen.findByRole('button', { name: 'Save' }));

		await waitFor(() => expect(createTicketMock).toHaveBeenCalledWith(expectedBody, TOKEN));
		expect(gotoMock).toHaveBeenCalledWith('/tickets/T99');
	});

	it('renders ApiErrorView when the server rejects a duplicate/invalid id', async () => {
		setToken(TOKEN);
		// The dry-run is the create path too, so id uniqueness/validity surfaces here as
		// the server-enforced ApiError.
		previewWriteMock.mockRejectedValue(
			new ApiError({
				code: 'write_conflict',
				message: 'A ticket with id T99 already exists.',
				status: 409
			})
		);
		render(Page, { props: { data: pageData() } });

		await submitValidForm();

		// The review dialog surfaces the error via ApiErrorView (code + message), and no
		// create is applied.
		expect(await screen.findByText('write_conflict')).toBeTruthy();
		expect(screen.getByText('A ticket with id T99 already exists.')).toBeTruthy();
		expect(createTicketMock).not.toHaveBeenCalled();
	});

	it('prompts for a missing token before it previews, then resumes once pasted', async () => {
		// No token held.
		previewWriteMock.mockResolvedValue(previewResult);
		render(Page, { props: { data: pageData() } });

		await submitValidForm();

		// The submit parked behind the prompt — nothing was previewed.
		expect(screen.getByText('Write token required')).toBeTruthy();
		expect(previewWriteMock).not.toHaveBeenCalled();

		// Storing the token resumes the parked dry-run.
		await fireEvent.input(screen.getByLabelText('Write token'), { target: { value: TOKEN } });
		await fireEvent.click(screen.getByRole('button', { name: 'Save token' }));

		expect(get(writeToken)).toBe(TOKEN);
		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));
		expect(previewWriteMock).toHaveBeenCalledWith(
			{ verb: 'create', body: expect.objectContaining({ id: 'T99' }) },
			TOKEN
		);
	});
});
