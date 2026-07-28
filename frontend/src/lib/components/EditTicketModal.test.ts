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

	// The token can go missing between reviewing the diff and clicking Save (the
	// sibling delete flow's own 401 clears the shared store) — `handleConfirm` must
	// close the review dialog before parking, or Save is left looking live behind a
	// backdrop hiding the very prompt that would let the user continue.
	it('closes the review dialog and asks for a token that goes missing before Save', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(PREVIEW);
		updateTicketMock.mockResolvedValue(APPLIED);
		render(EditTicketModal, { props: baseProps() });

		await fireEvent.click(saveChangesButton());
		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));
		expect(await screen.findByText('+new title')).toBeTruthy();

		// The token vanishes from under the open review dialog.
		clearToken();
		await fireEvent.click(confirmSaveButton());

		// The review dialog is gone rather than left open with a Save that does
		// nothing, and the token prompt — otherwise unreachable behind its backdrop —
		// is visible.
		expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
		expect(screen.getByText('Write token required')).toBeTruthy();
		expect(updateTicketMock).not.toHaveBeenCalled();

		await fireEvent.input(screen.getByLabelText('Write token'), { target: { value: TOKEN } });
		await fireEvent.click(screen.getByRole('button', { name: 'Save token' }));

		await waitFor(() => expect(updateTicketMock).toHaveBeenCalledTimes(1));
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
			new ApiError({ code: 'ticket_not_mutable', message: 'The lane owns it.', status: 409 })
		);
		render(EditTicketModal, { props: baseProps() });

		await fireEvent.click(saveChangesButton());

		expect(await screen.findByText('ticket_not_mutable')).toBeTruthy();
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

	// Once the PUT is out there is nothing left to call off, so every dismissal must be
	// refused until it settles — otherwise the user is told they cancelled a write that
	// still lands. The component-level tests only prove the prop disables buttons; this
	// proves `EditTicketModal` actually raises it around the write and passes it down.
	it('refuses every dismissal while the apply is in flight', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(PREVIEW);
		let settleApply: (result: WriteResult) => void = () => {};
		updateTicketMock.mockReturnValue(
			new Promise<WriteResult>((resolve) => {
				settleApply = resolve;
			})
		);
		const props = baseProps();
		render(EditTicketModal, { props });

		await fireEvent.click(saveChangesButton());
		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));
		await fireEvent.click(confirmSaveButton());
		await waitFor(() => expect(updateTicketMock).toHaveBeenCalledTimes(1));

		// The write is still in flight here — nothing has resolved it yet.
		const cancel = screen.getByRole('button', { name: 'Cancel' });
		expect(cancel.hasAttribute('disabled')).toBe(true);
		expect(confirmSaveButton().hasAttribute('disabled')).toBe(true);
		// `loading` covers only the dry-run, not the apply: the confirmed diff stays on
		// screen instead of being replaced by a "Loading preview…" spinner that would
		// describe a request that is not the one actually in flight.
		expect(screen.queryByText('Loading preview…')).toBeNull();
		expect(screen.getByText('+new title')).toBeTruthy();

		await fireEvent.click(cancel);
		await fireEvent.click(confirmSaveButton());
		await fireEvent.keyDown(window, { key: 'Escape' });
		// The host dialog's Close is reachable once the review dialog's controls are
		// all disabled, so it is guarded too.
		await fireEvent.click(screen.getByRole('button', { name: 'Close' }));

		// One confirmation stayed one write, and nothing closed over it.
		expect(updateTicketMock).toHaveBeenCalledTimes(1);
		expect(props.onClose).not.toHaveBeenCalled();
		expect(props.onSaved).not.toHaveBeenCalled();

		settleApply(APPLIED);

		await waitFor(() => expect(props.onSaved).toHaveBeenCalledTimes(1));
	});

	// The reset effect must not fire mid-apply: an SSE bump replaces the ticket in place
	// on any `invalidateAll()`, and resetting then would tear the review dialog down
	// around a PUT that is still in flight.
	it('does not reset the review while the apply is in flight, then applies cleanly', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(PREVIEW);
		let settleApply: (result: WriteResult) => void = () => {};
		updateTicketMock.mockReturnValue(
			new Promise<WriteResult>((resolve) => {
				settleApply = resolve;
			})
		);
		const props = baseProps();
		const { rerender } = render(EditTicketModal, { props });

		await fireEvent.click(saveChangesButton());
		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));
		await fireEvent.click(confirmSaveButton());
		await waitFor(() => expect(updateTicketMock).toHaveBeenCalledTimes(1));

		// Same ticket, new content — exactly what an SSE-driven reload delivers.
		await rerender({
			...props,
			ticket: { ...ticket, bodyMarkdown: '## Context\n\nChanged mid-apply.' }
		});

		// The dialog the write is running under is still there.
		expect(screen.getByRole('button', { name: 'Save' })).toBeTruthy();
		expect(updateTicketMock).toHaveBeenCalledTimes(1);

		settleApply(APPLIED);

		await waitFor(() => expect(props.onSaved).toHaveBeenCalledTimes(1));
	});

	// A dry-run that was abandoned must not report into a dialog nobody is looking at.
	it('discards an abandoned dry-run instead of reopening it on the next edit', async () => {
		setToken(TOKEN);
		let rejectPreview: (err: unknown) => void = () => {};
		previewWriteMock.mockReturnValueOnce(
			new Promise<WritePreview>((_resolve, reject) => {
				rejectPreview = reject;
			})
		);
		const props = baseProps();
		render(EditTicketModal, { props });

		await fireEvent.click(saveChangesButton());
		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));

		// Close while the dry-run is still out — allowed, since nothing is being written.
		await fireEvent.click(screen.getByRole('button', { name: 'Close' }));
		expect(props.onClose).toHaveBeenCalledTimes(1);

		rejectPreview(new ApiError({ code: 'internal_error', message: 'Boom.', status: 500 }));
		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));

		// The abandoned attempt's failure does not resurrect the review dialog, and the
		// form is not left disabled behind a request nobody is waiting for.
		expect(screen.queryByText('internal_error')).toBeNull();
		expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
		expect(saveChangesButton().hasAttribute('disabled')).toBe(false);
	});

	// Cancelling the review is reachable while a dry-run is still loading (the
	// spinner state), not just once it has landed. `handlePreviewCancel` did not
	// used to supersede that request, so its settling failure reopened the dialog
	// the user had just dismissed.
	it('discards an abandoned dry-run when cancelled while it is still loading', async () => {
		setToken(TOKEN);
		let rejectPreview: (err: unknown) => void = () => {};
		previewWriteMock.mockReturnValueOnce(
			new Promise<WritePreview>((_resolve, reject) => {
				rejectPreview = reject;
			})
		);
		render(EditTicketModal, { props: baseProps() });

		await fireEvent.click(saveChangesButton());
		expect(await screen.findByText('Loading preview…')).toBeTruthy();

		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
		expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();

		rejectPreview(new ApiError({ code: 'internal_error', message: 'Boom.', status: 500 }));
		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));

		// The abandoned attempt's failure does not resurrect the review dialog, and the
		// form is not left disabled behind a request nobody is waiting for.
		expect(screen.queryByText('internal_error')).toBeNull();
		expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
		expect(saveChangesButton().hasAttribute('disabled')).toBe(false);
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

		// The reviewed diff is gone, so there is no body left that a confirm could apply
		// to the ticket that replaced it — and the dialog says why rather than vanishing.
		expect(screen.queryByText('+new title')).toBeNull();
		expect(screen.getByText('ticket_changed_on_disk')).toBeTruthy();
		expect(confirmSaveButton().hasAttribute('disabled')).toBe(true);
		expect(updateTicketMock).not.toHaveBeenCalled();
		// The heading follows the new ticket, so the dialog is not still claiming to
		// edit the one whose diff was just discarded.
		expect(screen.getByRole('heading', { name: /T71/ })).toBeTruthy();
		// …and so do the FIELDS: `TicketForm` snapshots `initial` once, so without the
		// `{#key}` these would still hold T70's values while the dialog applies as T71.
		expect((screen.getByLabelText('Ticket id') as HTMLInputElement).value).toBe('T71');
		expect((screen.getByLabelText('Title') as HTMLInputElement).value).toBe('A different ticket');
	});

	// An id-only guard misses this: the layout `invalidateAll()`s on every SSE bump,
	// replacing `ticket` in place with the SAME id. A diff reviewed against the old
	// content would otherwise survive and overwrite the concurrent change.
	it('drops a reviewed diff when the same ticket changes underneath it', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(PREVIEW);
		const props = baseProps();
		const { rerender } = render(EditTicketModal, { props });

		await fireEvent.click(saveChangesButton());
		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));
		expect(screen.getByText('+new title')).toBeTruthy();

		// Same id, different content on disk.
		await rerender({
			...props,
			ticket: { ...ticket, bodyMarkdown: '## Context\n\nRewritten by someone else.' }
		});

		// The stale diff is dropped and replaced by an explanation, with Save inert — the
		// user is told why, instead of a dialog silently emptying or applying a diff that
		// no longer describes the write.
		expect(screen.queryByText('+new title')).toBeNull();
		expect(screen.getByText('ticket_changed_on_disk')).toBeTruthy();
		expect(confirmSaveButton().hasAttribute('disabled')).toBe(true);
		expect(updateTicketMock).not.toHaveBeenCalled();
	});

	// A write parked behind the token prompt sets neither `applying` nor `busy`, so
	// the reset effect used to drop it with nothing on screen saying so — pasting
	// the token afterwards did nothing.
	it('surfaces the ticket-changed error instead of silently dropping a parked write', async () => {
		const props = baseProps();
		const { rerender } = render(EditTicketModal, { props });

		await fireEvent.click(saveChangesButton());
		expect(screen.getByText('Write token required')).toBeTruthy();
		expect(previewWriteMock).not.toHaveBeenCalled();

		// The ticket changes underneath while the write is still parked — before any
		// request has even started.
		await rerender({ ...props, ticket: { ...ticket, id: 'T71', title: 'A different ticket' } });

		expect(screen.queryByText('Write token required')).toBeNull();
		expect(screen.getByText('ticket_changed_on_disk')).toBeTruthy();
	});

	// The route raises its own prompt for delete, and that one's `onSaved` knows
	// nothing about an edit parked here — so resumption watches the store, not one
	// prompt's callback.
	it('resumes a parked edit when the token arrives from elsewhere', async () => {
		previewWriteMock.mockResolvedValue(PREVIEW);
		render(EditTicketModal, { props: baseProps() });

		await fireEvent.click(saveChangesButton());
		expect(screen.getByText('Write token required')).toBeTruthy();
		expect(previewWriteMock).not.toHaveBeenCalled();

		// Stored by something other than this dialog's prompt.
		setToken(TOKEN);

		await waitFor(() => expect(previewWriteMock).toHaveBeenCalledTimes(1));
		expect(previewWriteMock).toHaveBeenCalledWith(
			{ verb: 'update', id: 'T70', body: UNCHANGED_BODY },
			TOKEN
		);
		expect(screen.queryByText('Write token required')).toBeNull();
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
