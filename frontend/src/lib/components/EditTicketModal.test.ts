import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Mock the barrel so no write ever leaves the test. The modal imports the write
// wrappers from `$lib/api`; `normalizeError` comes from `$lib/api/contracts`, which
// is a different module and stays real.
vi.mock('$lib/api', () => ({ previewWrite: vi.fn(), updateTicket: vi.fn() }));

import { previewWrite, updateTicket } from '$lib/api';
// The CLASS, imported directly (the barrel above is mocked away) — this is what the
// real client throws, so it is what the modal must normalize for `ApiErrorView`.
import { ApiError } from '$lib/api/errors';
import type { Ticket, TicketUpdate, WritePreview } from '$lib/api/models';
import EditTicketModal from '$lib/components/EditTicketModal.svelte';
import { clearToken, setToken } from '$lib/stores/writeToken';

const previewWriteMock = vi.mocked(previewWrite);
const updateTicketMock = vi.mocked(updateTicket);

const TOKEN = 'tok-abc';

const ticket: Ticket = {
	id: 'T42',
	title: 'Original title',
	status: 'todo',
	track: 'frontend',
	milestone: 'v2',
	runState: 'todo',
	dependsOn: ['T40', 'T41'],
	provides: ['An edit flow'],
	files: ['frontend/src/lib/components/EditTicketModal.svelte'],
	filePath: '/docs/planning/tickets/v2/T42.md',
	bodyMarkdown: '## Body\n',
	bodyHtml: '<h2>Body</h2>',
	raw: {}
};

/** Exactly what the form's untouched initial values map to on the wire. */
const EXPECTED_UPDATE: TicketUpdate = {
	title: 'Original title',
	track: 'frontend',
	milestone: 'v2',
	dependsOn: ['T40', 'T41'],
	provides: 'An edit flow',
	files: ['frontend/src/lib/components/EditTicketModal.svelte'],
	bodyMarkdown: '## Body\n'
};

const PREVIEW: WritePreview = {
	applied: false,
	ticketId: 'T42',
	diff: {
		ticketId: 'T42',
		files: [{ path: 'docs/planning/tickets/v2/T42.md', changeKind: 'modify', diff: '-a\n+b\n' }]
	},
	ticket: null
};

function baseProps() {
	return { ticket, open: true, onClose: vi.fn(), onSaved: vi.fn() };
}

function submitForm(): Promise<boolean> {
	return fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
}

describe('EditTicketModal', () => {
	beforeEach(() => {
		clearToken();
		previewWriteMock.mockReset();
		updateTicketMock.mockReset();
	});

	it('renders nothing while closed', () => {
		render(EditTicketModal, { props: { ...baseProps(), open: false } });

		expect(screen.queryByRole('dialog')).toBeNull();
		expect(screen.queryByRole('button', { name: 'Save changes' })).toBeNull();
	});

	it('seeds the form from the ticket, collapsing the provides list to its scalar', () => {
		render(EditTicketModal, { props: baseProps() });

		expect((screen.getByLabelText('Ticket id') as HTMLInputElement).value).toBe('T42');
		expect((screen.getByLabelText('Title') as HTMLInputElement).value).toBe('Original title');
		expect((screen.getByLabelText('Depends on') as HTMLTextAreaElement).value).toBe('T40\nT41');
		expect((screen.getByLabelText('Provides') as HTMLInputElement).value).toBe('An edit flow');
	});

	it('submits a dry-run preview and shows the diff, then writes on confirm', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(PREVIEW);
		updateTicketMock.mockResolvedValue({ ...PREVIEW, applied: true });
		const props = baseProps();
		render(EditTicketModal, { props });

		await submitForm();

		await waitFor(() =>
			expect(previewWriteMock).toHaveBeenCalledWith(
				{ verb: 'update', id: 'T42', body: EXPECTED_UPDATE },
				TOKEN
			)
		);
		// Nothing is written by the dry-run itself.
		expect(updateTicketMock).not.toHaveBeenCalled();
		await screen.findByText('docs/planning/tickets/v2/T42.md');

		await fireEvent.click(screen.getByRole('button', { name: 'Save' }));

		await waitFor(() =>
			expect(updateTicketMock).toHaveBeenCalledWith('T42', EXPECTED_UPDATE, TOKEN)
		);
		expect(props.onSaved).toHaveBeenCalledTimes(1);
	});

	it('sends the edited title rather than the seeded one', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(PREVIEW);
		render(EditTicketModal, { props: baseProps() });

		await fireEvent.input(screen.getByLabelText('Title'), { target: { value: 'New title' } });
		await submitForm();

		await waitFor(() =>
			expect(previewWriteMock).toHaveBeenCalledWith(
				{ verb: 'update', id: 'T42', body: { ...EXPECTED_UPDATE, title: 'New title' } },
				TOKEN
			)
		);
	});

	it('asks for a token before previewing when none is held, then resumes the submit', async () => {
		previewWriteMock.mockResolvedValue(PREVIEW);
		render(EditTicketModal, { props: baseProps() });

		await submitForm();

		// The prompt comes first — no dry-run is attempted without a token.
		expect(previewWriteMock).not.toHaveBeenCalled();
		const field = screen.getByLabelText('Write token');
		await fireEvent.input(field, { target: { value: TOKEN } });
		await fireEvent.click(screen.getByRole('button', { name: 'Save token' }));

		await waitFor(() =>
			expect(previewWriteMock).toHaveBeenCalledWith(
				{ verb: 'update', id: 'T42', body: EXPECTED_UPDATE },
				TOKEN
			)
		);
		expect(screen.queryByLabelText('Write token')).toBeNull();
	});

	it('renders a failed dry-run through the diff dialog error view', async () => {
		setToken(TOKEN);
		previewWriteMock.mockRejectedValue(
			new ApiError({ code: 'write_conflict', message: 'The ticket changed on disk.', status: 409 })
		);
		render(EditTicketModal, { props: baseProps() });

		await submitForm();

		expect(await screen.findByText('write_conflict')).toBeTruthy();
		expect(screen.getByText('The ticket changed on disk.')).toBeTruthy();
		expect(screen.getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(true);
	});

	it('reports a failed write on the form and does not call onSaved', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(PREVIEW);
		updateTicketMock.mockRejectedValue(
			new ApiError({ code: 'write_token_invalid', message: 'Bad token.', status: 401 })
		);
		const props = baseProps();
		render(EditTicketModal, { props });

		await submitForm();
		await fireEvent.click(await screen.findByRole('button', { name: 'Save' }));

		expect(await screen.findByText('write_token_invalid')).toBeTruthy();
		expect(screen.getByText('Bad token.')).toBeTruthy();
		expect(props.onSaved).not.toHaveBeenCalled();
		// The diff closed, so the form is back and the edit can be retried.
		expect(screen.getByRole('button', { name: 'Save changes' })).toBeTruthy();
	});

	it('closes without writing anything when cancelled', async () => {
		setToken(TOKEN);
		const props = baseProps();
		render(EditTicketModal, { props });

		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

		expect(props.onClose).toHaveBeenCalledTimes(1);
		expect(previewWriteMock).not.toHaveBeenCalled();
		expect(updateTicketMock).not.toHaveBeenCalled();
	});

	it('backing out of the diff writes nothing and keeps the modal open', async () => {
		setToken(TOKEN);
		previewWriteMock.mockResolvedValue(PREVIEW);
		const props = baseProps();
		render(EditTicketModal, { props });

		await submitForm();
		await screen.findByRole('button', { name: 'Save' });
		// The diff's own Cancel is the second one on screen (the modal header has one).
		const cancels = screen.getAllByRole('button', { name: 'Cancel' });
		await fireEvent.click(cancels[cancels.length - 1]);

		expect(updateTicketMock).not.toHaveBeenCalled();
		expect(props.onClose).not.toHaveBeenCalled();
		expect(screen.getByRole('button', { name: 'Save changes' })).toBeTruthy();
	});
});
