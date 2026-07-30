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

	// A rejected token on the DRY-RUN is not terminal (see the repo write-token rule):
	// drop it, say WHY, and resume the same preview once a fresh one is pasted.
	it('drops a rejected token on the dry-run, explains it, and resumes the preview', async () => {
		setToken(TOKEN);
		previewWriteMock.mockRejectedValueOnce(
			new ApiError({ code: 'write_token_invalid', message: 'Bad token.', status: 401 })
		);
		previewWriteMock.mockResolvedValueOnce(previewResult);
		render(Page, { props: { data: pageData() } });

		const expectedBody = {
			id: 'T99',
			title: 'A brand new ticket',
			dependsOn: [],
			provides: '',
			files: [],
			bodyMarkdown: ''
		};

		await submitValidForm();

		// The prompt is back and explains WHY, not a first-time "no token" panel.
		expect(await screen.findByText('Write token required')).toBeTruthy();
		expect(screen.getByRole('alert').textContent).toContain('rejected');
		// The known-bad token was discarded, not left to fail every retry.
		expect(get(writeToken)).toBeNull();

		await fireEvent.input(screen.getByLabelText('Write token'), {
			target: { value: 'fresh-token' }
		});
		await fireEvent.click(screen.getByRole('button', { name: 'Save token' }));

		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(2));
		// The resumed dry-run sends the SAME full body, only with the fresh token.
		expect(previewWriteMock).toHaveBeenLastCalledWith(
			{ verb: 'create', body: expectedBody },
			'fresh-token'
		);
		expect(screen.queryByText('Write token required')).toBeNull();
	});

	// A rejected token on the APPLY re-raises the prompt and, once a fresh one is pasted,
	// resumes with the REVIEWED body verbatim — written exactly once, never re-derived.
	it('resumes the apply with the reviewed body after a rejected token, writing it once', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(previewResult);
		createTicketMock.mockRejectedValueOnce(
			new ApiError({ code: 'write_token_invalid', message: 'Bad token.', status: 401 })
		);
		createTicketMock.mockResolvedValueOnce({
			applied: true,
			ticketId: 'T99',
			diff: { ticketId: 'T99' },
			ticket: null
		});
		render(Page, { props: { data: pageData() } });

		const expectedBody = {
			id: 'T99',
			title: 'A brand new ticket',
			dependsOn: [],
			provides: '',
			files: [],
			bodyMarkdown: ''
		};

		await submitValidForm();
		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));
		await fireEvent.click(await screen.findByRole('button', { name: 'Save' }));

		// The apply's 401 raised the prompt; nothing was written or navigated yet.
		await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
		expect(get(writeToken)).toBeNull();
		expect(gotoMock).not.toHaveBeenCalled();

		await fireEvent.input(screen.getByLabelText('Write token'), {
			target: { value: 'fresh-token' }
		});
		await fireEvent.click(screen.getByRole('button', { name: 'Save token' }));

		await waitFor(() => expect(gotoMock).toHaveBeenCalledWith('/tickets/T99'));
		// The rejected attempt did not also land: one confirmation is one create, and the
		// retry carried the previewed body with the fresh token.
		expect(createTicketMock).toHaveBeenCalledTimes(2);
		expect(createTicketMock).toHaveBeenLastCalledWith(expectedBody, 'fresh-token');
	});

	// Cancelling a dry-run that is still in flight must supersede it: its late failure
	// cannot reopen the dialog the user just dismissed or strand the form disabled.
	it('cancels an in-flight dry-run and does not reopen it when it later fails', async () => {
		setToken(TOKEN);
		let rejectPreview: (err: unknown) => void = () => {};
		previewWriteMock.mockReturnValueOnce(
			new Promise<WriteResult>((_resolve, reject) => {
				rejectPreview = reject;
			})
		);
		render(Page, { props: { data: pageData() } });

		await submitValidForm();
		expect(await screen.findByText('Loading preview…')).toBeTruthy();

		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
		expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();

		rejectPreview(new ApiError({ code: 'internal_error', message: 'Boom.', status: 500 }));
		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));

		// The abandoned failure does not resurrect the review dialog or its error, and the
		// form is usable again rather than stuck disabled behind a request nobody awaits.
		expect(screen.queryByText('internal_error')).toBeNull();
		expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
		expect(
			(screen.getByRole('button', { name: 'Create ticket' }) as HTMLButtonElement).disabled
		).toBe(false);
	});

	// The `seq !== attempt` guard must not stand between a rejected token and `clearToken()`:
	// the credential is wrong for every write on the page, so even an abandoned dry-run's
	// 401 must still drop it rather than leave a known-bad token in sessionStorage.
	it('drops a rejected token even when the dry-run reporting it was cancelled', async () => {
		setToken(TOKEN);
		let rejectPreview: (err: unknown) => void = () => {};
		previewWriteMock.mockReturnValueOnce(
			new Promise<WriteResult>((_resolve, reject) => {
				rejectPreview = reject;
			})
		);
		render(Page, { props: { data: pageData() } });

		await submitValidForm();
		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));

		// Cancel abandons the dry-run before it settles.
		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

		rejectPreview(
			new ApiError({ code: 'write_token_invalid', message: 'Bad token.', status: 401 })
		);
		await waitFor(() => expect(get(writeToken)).toBeNull());
	});
});
