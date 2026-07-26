import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'svelte';
import type { ApiError } from '$lib/api/contracts';
import type { WritePreview } from '$lib/api';
import DiffPreviewModal from '$lib/components/DiffPreviewModal.svelte';

// A dry-run body in the real NESTED shape: `WritePreview` is the `WriteResult`
// envelope, and the per-file unified-diff text lives at `diff.files[].diff`.
const PREVIEW: WritePreview = {
	applied: false,
	ticketId: 'T69',
	changedFiles: ['docs/planning/tickets/v2/T69.md', 'docs/planning/notes.md'],
	ticket: null,
	diff: {
		ticketId: 'T69',
		files: [
			{
				path: 'docs/planning/tickets/v2/T69.md',
				changeKind: 'modify',
				diff: [
					'--- a/docs/planning/tickets/v2/T69.md',
					'+++ b/docs/planning/tickets/v2/T69.md',
					'@@ -1,3 +1,3 @@',
					' # T69',
					'-stale summary',
					'+fresh summary',
					''
				].join('\n')
			},
			{
				path: 'docs/planning/notes.md',
				changeKind: 'create',
				diff: [
					'--- /dev/null',
					'+++ b/docs/planning/notes.md',
					'@@ -0,0 +1 @@',
					'+brand new',
					''
				].join('\n')
			}
		]
	}
};

/** Baseline props: an open modal showing a fetched preview. */
function props(
	overrides: Partial<ComponentProps<typeof DiffPreviewModal>> = {}
): ComponentProps<typeof DiffPreviewModal> {
	return {
		open: true,
		preview: PREVIEW,
		loading: false,
		error: null,
		onConfirm: vi.fn(),
		onCancel: vi.fn(),
		...overrides
	};
}

function saveButton(): HTMLButtonElement {
	return screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement;
}

describe('DiffPreviewModal', () => {
	it('renders nothing until it is opened', () => {
		render(DiffPreviewModal, { props: props({ open: false }) });

		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('names the dialog and marks it modal so assistive tech treats it as one', () => {
		render(DiffPreviewModal, { props: props() });

		const dialog = screen.getByRole('dialog', { name: 'Review changes' });
		expect(dialog.getAttribute('aria-modal')).toBe('true');
	});

	it('shows every previewed file with its path, change kind and diff lines', () => {
		render(DiffPreviewModal, { props: props() });

		expect(screen.getByText('docs/planning/tickets/v2/T69.md')).toBeTruthy();
		expect(screen.getByText('modify')).toBeTruthy();
		expect(screen.getByText('docs/planning/notes.md')).toBeTruthy();
		expect(screen.getByText('create')).toBeTruthy();

		expect(screen.getByText('@@ -1,3 +1,3 @@')).toBeTruthy();
		expect(screen.getByText('-stale summary')).toBeTruthy();
		expect(screen.getByText('+fresh summary')).toBeTruthy();
		expect(screen.getByText('+brand new')).toBeTruthy();
	});

	it('color-codes added, removed and hunk lines differently', () => {
		render(DiffPreviewModal, { props: props() });

		const added = screen.getByText('+fresh summary').className;
		const removed = screen.getByText('-stale summary').className;
		const hunk = screen.getByText('@@ -1,3 +1,3 @@').className;

		expect(removed).toContain('text-danger');
		expect(added).not.toBe(removed);
		expect(hunk).not.toBe(added);
		expect(hunk).not.toBe(removed);
	});

	it('shows a spinner and no diff while the preview is loading, with save gated', () => {
		render(DiffPreviewModal, { props: props({ loading: true }) });

		expect(screen.getByRole('status').textContent).toContain('Loading preview');
		expect(screen.queryByText('+fresh summary')).toBeNull();
		expect(saveButton().disabled).toBe(true);
	});

	it('renders the ApiErrorView instead of a diff when the dry-run failed, with save gated', () => {
		const error: ApiError = {
			code: 'write_gate_blocked',
			message: 'Ticket is not editable.',
			hint: 'Only todo tickets can be written.'
		};
		render(DiffPreviewModal, { props: props({ error, preview: null }) });

		expect(screen.getByText('write_gate_blocked')).toBeTruthy();
		expect(screen.getByText('Ticket is not editable.')).toBeTruthy();
		expect(screen.getByText('Only todo tickets can be written.')).toBeTruthy();
		expect(saveButton().disabled).toBe(true);
	});

	it('gates save on the error even when a stale preview is still in hand', () => {
		const error: ApiError = { code: 'network_error', message: 'Request failed.' };
		render(DiffPreviewModal, { props: props({ error }) });

		expect(saveButton().disabled).toBe(true);
		expect(screen.queryByText('+fresh summary')).toBeNull();
	});

	it("dismisses via the error view's button, since the dialog cannot re-run the dry-run", async () => {
		const onCancel = vi.fn();
		const error: ApiError = { code: 'network_error', message: 'Request failed.' };
		render(DiffPreviewModal, { props: props({ error, preview: null, onCancel }) });

		await fireEvent.click(screen.getByRole('button', { name: 'Reload' }));

		expect(onCancel).toHaveBeenCalledTimes(1);
	});

	it('says there is no preview yet and gates save when none has been fetched', () => {
		render(DiffPreviewModal, { props: props({ preview: null }) });

		expect(screen.getByText('No preview to review yet.')).toBeTruthy();
		expect(saveButton().disabled).toBe(true);
	});

	it('says the write changes nothing and gates save when the preview has an empty file list', () => {
		const empty: WritePreview = {
			applied: false,
			ticketId: 'T69',
			ticket: null,
			diff: { ticketId: 'T69', files: [] }
		};
		render(DiffPreviewModal, { props: props({ preview: empty }) });

		expect(screen.getByText('This write would not change any files.')).toBeTruthy();
		expect(saveButton().disabled).toBe(true);
	});

	it('survives a preview whose optional files list is absent entirely', () => {
		const noFiles: WritePreview = {
			applied: false,
			ticketId: 'T69',
			ticket: null,
			diff: { ticketId: 'T69' }
		};
		render(DiffPreviewModal, { props: props({ preview: noFiles }) });

		expect(screen.getByText('This write would not change any files.')).toBeTruthy();
		expect(saveButton().disabled).toBe(true);
	});

	it('fires onConfirm once when save is clicked on a reviewable preview', async () => {
		const onConfirm = vi.fn();
		render(DiffPreviewModal, { props: props({ onConfirm }) });

		expect(saveButton().disabled).toBe(false);
		await fireEvent.click(saveButton());

		expect(onConfirm).toHaveBeenCalledTimes(1);
	});

	it('fires onCancel from the cancel button, Escape, and a backdrop click', async () => {
		const onCancel = vi.fn();
		const onConfirm = vi.fn();
		const { container } = render(DiffPreviewModal, { props: props({ onCancel, onConfirm }) });

		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
		await fireEvent.keyDown(window, { key: 'Escape' });
		const backdrop = container.querySelector('[data-testid="modal-backdrop"]');
		if (!backdrop) throw new Error('expected the dialog to render a backdrop');
		await fireEvent.click(backdrop);

		expect(onCancel).toHaveBeenCalledTimes(3);
		// Dismissing is never a write.
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it('opens with focus on cancel, so a stray Enter dismisses rather than writes', async () => {
		render(DiffPreviewModal, { props: props() });

		expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }));
	});

	it('ignores keys other than Escape while open', async () => {
		const onCancel = vi.fn();
		render(DiffPreviewModal, { props: props({ onCancel }) });

		await fireEvent.keyDown(window, { key: 'Enter' });

		expect(onCancel).not.toHaveBeenCalled();
	});

	it('ignores Escape once closed', async () => {
		const onCancel = vi.fn();
		render(DiffPreviewModal, { props: props({ open: false, onCancel }) });

		await fireEvent.keyDown(window, { key: 'Escape' });

		expect(onCancel).not.toHaveBeenCalled();
	});
});
