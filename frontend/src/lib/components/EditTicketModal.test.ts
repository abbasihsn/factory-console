import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// The modal is the ONE piece of the edit flow that talks to the API, so the barrel
// is stubbed down to the two wrappers it uses. The write-token store is deliberately
// NOT mocked: it is pure, works under jsdom, and using the real one means the
// missing-token detour is exercised end-to-end through `WriteTokenPrompt`.
vi.mock('$lib/api', () => ({ previewWrite: vi.fn(), updateTicket: vi.fn() }));

import { previewWrite, updateTicket } from '$lib/api';
import { ApiError } from '$lib/api/errors';
import type { Ticket, WritePreview, WriteResult } from '$lib/api';
import EditTicketModal from '$lib/components/EditTicketModal.svelte';
import { clearToken, setToken, writeToken } from '$lib/stores/writeToken';

const previewWriteMock = vi.mocked(previewWrite);
const updateTicketMock = vi.mocked(updateTicket);

const TOKEN = 'test-write-token';

const ticket: Ticket = {
	id: 'T70',
	title: 'Wire gated edit + delete',
	status: 'todo',
	track: 'frontend',
	milestone: 'v2',
	runState: 'todo',
	dependsOn: ['T68', 'T69'],
	provides: ['Edit affordances'],
	files: ['frontend/src/routes/tickets/[id]/+page.svelte'],
	filePath: '/docs/planning/tickets/v2/T70-detail-edit-delete.md',
	bodyMarkdown: '## Context\n\nThe body as loaded.',
	bodyHtml: '<h2>Context</h2>',
	raw: {}
};

/** The PUT body the untouched form produces for `ticket`. */
const UNCHANGED_BODY = {
	title: 'Wire gated edit + delete',
	track: 'frontend',
	milestone: 'v2',
	dependsOn: ['T68', 'T69'],
	provides: 'Edit affordances',
	files: ['frontend/src/routes/tickets/[id]/+page.svelte'],
	bodyMarkdown: '## Context\n\nThe body as loaded.'
};

const PREVIEW: WritePreview = {
	applied: false,
	ticketId: 'T70',
	diff: {
		ticketId: 'T70',
		files: [
			{
				path: 'docs/planning/tickets/v2/T70-detail-edit-delete.md',
				changeKind: 'modify',
				diff: '@@ -1 +1 @@\n-old title\n+new title\n'
			}
		]
	},
	ticket: null
};

const APPLIED: WriteResult = { ...PREVIEW, applied: true, ticket };

function baseProps() {
	return { ticket, open: true, onClose: vi.fn(), onSaved: vi.fn() };
}

function saveChangesButton(): HTMLElement {
	return screen.getByRole('button', { name: 'Save changes' });
}

/** The review dialog's confirm button (the form's submit is "Save changes"). */
function confirmSaveButton(): HTMLElement {
	return screen.getByRole('button', { name: 'Save' });
}

describe('EditTicketModal', () => {
	beforeEach(() => {
		previewWriteMock.mockReset();
		updateTicketMock.mockReset();
		clearToken();
	});

	it('renders nothing while closed', () => {
		render(EditTicketModal, { props: { ...baseProps(), open: false } });

		expect(screen.queryByRole('dialog')).toBeNull();
		expect(screen.queryByRole('button', { name: 'Save changes' })).toBeNull();
	});

	it('seeds the form from the ticket, with the list fields as newline text', () => {
		setToken(TOKEN);
		render(EditTicketModal, { props: baseProps() });

		expect((screen.getByLabelText('Ticket id') as HTMLInputElement).value).toBe('T70');
		expect((screen.getByLabelText('Ticket id') as HTMLInputElement).readOnly).toBe(true);
		expect((screen.getByLabelText('Title') as HTMLInputElement).value).toBe(
			'Wire gated edit + delete'
		);
		expect((screen.getByLabelText('Depends on') as HTMLTextAreaElement).value).toBe('T68\nT69');
		// `provides` is a scalar on the wire, so the single-element read list joins
		// back to exactly the stored value.
		expect((screen.getByLabelText('Provides') as HTMLInputElement).value).toBe('Edit affordances');
	});

	it('dry-runs the edit on submit and shows the returned diff', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(PREVIEW);
		render(EditTicketModal, { props: baseProps() });

		await fireEvent.input(screen.getByLabelText('Title'), { target: { value: 'Renamed' } });
		await fireEvent.click(saveChangesButton());

		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));
		expect(previewWriteMock).toHaveBeenCalledWith(
			{ verb: 'update', id: 'T70', body: { ...UNCHANGED_BODY, title: 'Renamed' } },
			TOKEN
		);
		// Nothing was written by the dry-run.
		expect(updateTicketMock).not.toHaveBeenCalled();
		expect(await screen.findByText('+new title')).toBeTruthy();
	});

	it('echoes track and milestone so a PUT cannot null them out', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(PREVIEW);
		render(EditTicketModal, { props: baseProps() });

		await fireEvent.click(saveChangesButton());

		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));
		const [write] = previewWriteMock.mock.calls[0];
		expect(write).toMatchObject({ body: { track: 'frontend', milestone: 'v2' } });
	});

	it('applies the reviewed body with the token on confirm, then reports saved', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(PREVIEW);
		updateTicketMock.mockResolvedValue(APPLIED);
		const props = baseProps();
		render(EditTicketModal, { props });

		await fireEvent.input(screen.getByLabelText('Title'), { target: { value: 'Renamed' } });
		await fireEvent.click(saveChangesButton());
		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));

		await fireEvent.click(confirmSaveButton());

		await waitFor(() => expect(props.onSaved).toHaveBeenCalledTimes(1));
		// The applied body is the one that was previewed, verbatim.
		expect(updateTicketMock).toHaveBeenCalledWith(
			'T70',
			{ ...UNCHANGED_BODY, title: 'Renamed' },
			TOKEN
		);
		expect(props.onClose).not.toHaveBeenCalled();
	});

	it('prompts for the missing token instead of dry-running, then resumes', async () => {
		previewWriteMock.mockResolvedValue(PREVIEW);
		render(EditTicketModal, { props: baseProps() });

		await fireEvent.click(saveChangesButton());

		expect(screen.getByText('Write token required')).toBeTruthy();
		expect(previewWriteMock).not.toHaveBeenCalled();
		// The submitted edit is still held: the form is not replaced by the prompt.
		expect(saveChangesButton()).toBeTruthy();

		await fireEvent.input(screen.getByLabelText('Write token'), { target: { value: TOKEN } });
		await fireEvent.click(screen.getByRole('button', { name: 'Save token' }));

		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));
		expect(previewWriteMock).toHaveBeenCalledWith(
			{ verb: 'update', id: 'T70', body: UNCHANGED_BODY },
			TOKEN
		);
		expect(screen.queryByText('Write token required')).toBeNull();
	});

	it('renders a failed dry-run as an ApiErrorView with save inert', async () => {
		setToken(TOKEN);
		previewWriteMock.mockRejectedValue(
			new ApiError({ code: 'ticket_not_editable', message: 'The lane owns it.', status: 409 })
		);
		render(EditTicketModal, { props: baseProps() });

		await fireEvent.click(saveChangesButton());

		expect(await screen.findByText('ticket_not_editable')).toBeTruthy();
		expect(screen.getByText('The lane owns it.')).toBeTruthy();
		expect(confirmSaveButton().hasAttribute('disabled')).toBe(true);
	});

	it('renders a failed apply as an ApiErrorView and does not report saved', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(PREVIEW);
		updateTicketMock.mockRejectedValue(
			new ApiError({ code: 'write_conflict', message: 'Changed on disk.', status: 409 })
		);
		const props = baseProps();
		render(EditTicketModal, { props });

		await fireEvent.click(saveChangesButton());
		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));
		await fireEvent.click(confirmSaveButton());

		expect(await screen.findByText('write_conflict')).toBeTruthy();
		expect(screen.getByText('Changed on disk.')).toBeTruthy();
		expect(props.onSaved).not.toHaveBeenCalled();
	});

	it('cancelling the review writes nothing and keeps the edited form', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(PREVIEW);
		const props = baseProps();
		render(EditTicketModal, { props });

		await fireEvent.input(screen.getByLabelText('Title'), { target: { value: 'Renamed' } });
		await fireEvent.click(saveChangesButton());
		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));

		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

		expect(updateTicketMock).not.toHaveBeenCalled();
		expect(props.onSaved).not.toHaveBeenCalled();
		expect(props.onClose).not.toHaveBeenCalled();
		expect((screen.getByLabelText('Title') as HTMLInputElement).value).toBe('Renamed');
	});

	// The 401 detour is the seam where the flow hands control back to the prompt and
	// then resumes: the token is dropped, the edit is held, and the write must go
	// out again — once — with the token pasted next.
	describe('when the held token is rejected', () => {
		it('drops the token, says so, and resumes the dry-run with the new one', async () => {
			setToken(TOKEN);
			previewWriteMock.mockRejectedValueOnce(
				new ApiError({ code: 'write_token_invalid', message: 'Bad token.', status: 401 })
			);
			previewWriteMock.mockResolvedValueOnce(PREVIEW);
			render(EditTicketModal, { props: baseProps() });

			await fireEvent.click(saveChangesButton());

			// The prompt is back, and it explains WHY rather than looking like a
			// first-time request for a token nobody had entered.
			expect(await screen.findByText('Write token required')).toBeTruthy();
			expect(screen.getByRole('alert').textContent).toContain('rejected');
			// The rejected token was discarded, not left to fail every retry.
			expect(get(writeToken)).toBeNull();

			await fireEvent.input(screen.getByLabelText('Write token'), {
				target: { value: 'fresh-token' }
			});
			await fireEvent.click(screen.getByRole('button', { name: 'Save token' }));

			await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(2));
			expect(previewWriteMock).toHaveBeenLastCalledWith(
				{ verb: 'update', id: 'T70', body: UNCHANGED_BODY },
				'fresh-token'
			);
			expect(await screen.findByText('+new title')).toBeTruthy();
		});

		it('resumes the APPLY with the reviewed body, writing it exactly once', async () => {
			setToken(TOKEN);
			previewWriteMock.mockResolvedValue(PREVIEW);
			updateTicketMock.mockRejectedValueOnce(
				new ApiError({ code: 'write_token_invalid', message: 'Bad token.', status: 401 })
			);
			updateTicketMock.mockResolvedValueOnce(APPLIED);
			const props = baseProps();
			render(EditTicketModal, { props });

			await fireEvent.input(screen.getByLabelText('Title'), { target: { value: 'Renamed' } });
			await fireEvent.click(saveChangesButton());
			await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));
			await fireEvent.click(confirmSaveButton());

			await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
			expect(get(writeToken)).toBeNull();
			expect(props.onSaved).not.toHaveBeenCalled();

			await fireEvent.input(screen.getByLabelText('Write token'), {
				target: { value: 'fresh-token' }
			});
			await fireEvent.click(screen.getByRole('button', { name: 'Save token' }));

			await waitFor(() => expect(props.onSaved).toHaveBeenCalledTimes(1));
			// The retry sent the body that was REVIEWED, and the rejected attempt did
			// not also land: one confirmation is one write.
			expect(updateTicketMock).toHaveBeenCalledTimes(2);
			expect(updateTicketMock).toHaveBeenLastCalledWith(
				'T70',
				{ ...UNCHANGED_BODY, title: 'Renamed' },
				'fresh-token'
			);
		});

		// The 401 branch tears the review dialog down to raise the prompt. Whatever
		// fails NEXT still has to be visible, or the write fails in silence and the
		// edit looks applied.
		it('still shows a non-401 failure of the resumed apply', async () => {
			setToken(TOKEN);
			previewWriteMock.mockResolvedValue(PREVIEW);
			updateTicketMock.mockRejectedValueOnce(
				new ApiError({ code: 'write_token_invalid', message: 'Bad token.', status: 401 })
			);
			updateTicketMock.mockRejectedValueOnce(
				new ApiError({ code: 'write_conflict', message: 'Changed on disk.', status: 409 })
			);
			const props = baseProps();
			render(EditTicketModal, { props });

			await fireEvent.click(saveChangesButton());
			await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));
			await fireEvent.click(confirmSaveButton());
			await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());

			await fireEvent.input(screen.getByLabelText('Write token'), {
				target: { value: 'fresh-token' }
			});
			await fireEvent.click(screen.getByRole('button', { name: 'Save token' }));

			expect(await screen.findByText('write_conflict')).toBeTruthy();
			expect(screen.getByText('Changed on disk.')).toBeTruthy();
			expect(props.onSaved).not.toHaveBeenCalled();
		});
	});

	// The route reuses this instance across a params-only navigation, so `ticket` can
	// be swapped under an in-progress edit. `applyEdit` sends the CURRENT `ticket.id`,
	// so a diff reviewed for one ticket must never survive into another.
	it('drops an in-progress review when the ticket is replaced', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(PREVIEW);
		const props = baseProps();
		const { rerender } = render(EditTicketModal, { props });

		await fireEvent.click(saveChangesButton());
		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));
		expect(screen.getByText('+new title')).toBeTruthy();

		await rerender({ ...props, ticket: { ...ticket, id: 'T71', title: 'A different ticket' } });

		// The review dialog is gone with it, so there is no reviewed body left that a
		// confirm could apply to the ticket that replaced it.
		expect(screen.queryByText('+new title')).toBeNull();
		expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
		expect(updateTicketMock).not.toHaveBeenCalled();
		// The heading follows the new ticket, so the dialog is not still claiming to
		// edit the one whose diff was just discarded.
		expect(screen.getByRole('heading', { name: /T71/ })).toBeTruthy();
	});

	it('closing writes nothing and reports the close', async () => {
		setToken(TOKEN);
		const props = baseProps();
		render(EditTicketModal, { props });

		await fireEvent.click(screen.getByRole('button', { name: 'Close' }));

		expect(props.onClose).toHaveBeenCalledTimes(1);
		expect(previewWriteMock).not.toHaveBeenCalled();
		expect(updateTicketMock).not.toHaveBeenCalled();
	});
});
