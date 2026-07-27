import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { ApiError } from '$lib/api/contracts';
import type { WritePreview } from '$lib/api/models';
import DiffPreviewModal from '$lib/components/DiffPreviewModal.svelte';

// A dry-run result covers every file the write would touch, so the fixture is
// deliberately multi-file with two different change kinds.
const PREVIEW: WritePreview = {
	applied: false,
	ticketId: 'T42',
	changedFiles: ['docs/planning/tickets/T42.md', 'docs/planning/tickets.json'],
	diff: {
		ticketId: 'T42',
		files: [
			{
				path: 'docs/planning/tickets/T42.md',
				changeKind: 'modify',
				diff: '--- a/docs/planning/tickets/T42.md\n+++ b/docs/planning/tickets/T42.md\n@@ -1,2 +1,2 @@\n-old title\n+new title\n context\n'
			},
			{
				path: 'docs/planning/tickets.json',
				changeKind: 'create',
				diff: '+++ b/docs/planning/tickets.json\n+{}\n'
			}
		]
	},
	ticket: null
};

const ERROR: ApiError = {
	code: 'write_conflict',
	message: 'The ticket changed on disk.',
	hint: 'Reload and try again.'
};

function baseProps() {
	return {
		open: true,
		preview: PREVIEW,
		loading: false,
		error: null,
		onConfirm: vi.fn(),
		onCancel: vi.fn()
	};
}

function saveButton(): HTMLElement {
	return screen.getByRole('button', { name: 'Save' });
}

function diffLines(container: HTMLElement): { text: string; className: string }[] {
	return Array.from(container.querySelectorAll('pre span')).map((span) => ({
		text: span.textContent ?? '',
		className: span.className
	}));
}

describe('DiffPreviewModal', () => {
	it('renders nothing while closed', () => {
		render(DiffPreviewModal, { props: { ...baseProps(), open: false } });

		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('renders one section per previewed file with its path and change kind', () => {
		render(DiffPreviewModal, { props: baseProps() });

		expect(screen.getByRole('dialog').getAttribute('aria-modal')).toBe('true');
		expect(screen.getByText('docs/planning/tickets/T42.md')).toBeTruthy();
		expect(screen.getByText('modify')).toBeTruthy();
		expect(screen.getByText('docs/planning/tickets.json')).toBeTruthy();
		expect(screen.getByText('create')).toBeTruthy();
	});

	it('color-codes each diff line by kind across all files', () => {
		const { container } = render(DiffPreviewModal, { props: baseProps() });

		const lines = diffLines(container);
		expect(lines.map((line) => line.text)).toEqual([
			'--- a/docs/planning/tickets/T42.md',
			'+++ b/docs/planning/tickets/T42.md',
			'@@ -1,2 +1,2 @@',
			'-old title',
			'+new title',
			' context',
			'+++ b/docs/planning/tickets.json',
			'+{}'
		]);

		const byText = new Map(lines.map((line) => [line.text, line.className]));
		expect(byText.get('+new title')).toContain('text-emerald-700');
		expect(byText.get('-old title')).toContain('text-danger');
		expect(byText.get('@@ -1,2 +1,2 @@')).toContain('text-accent');
		expect(byText.get('+++ b/docs/planning/tickets.json')).toContain('text-muted');
		expect(byText.get(' context')).toContain('text-text');
	});

	it('makes the diff region focusable and reachable by keyboard', () => {
		render(DiffPreviewModal, { props: baseProps() });

		const region = screen.getByRole('region');
		expect(region.getAttribute('tabindex')).toBe('0');
		expect(region.getAttribute('aria-labelledby')).toBe('diff-preview-title');
	});

	it('says so instead of rendering an empty diff when the preview has no files', () => {
		const preview: WritePreview = { applied: false, ticketId: 'T42', diff: { ticketId: 'T42' } };
		const { container } = render(DiffPreviewModal, { props: { ...baseProps(), preview } });

		expect(screen.getByText('No file changes in this preview.')).toBeTruthy();
		expect(diffLines(container)).toEqual([]);
		// An empty file list is still a valid preview — saving stays available.
		expect(saveButton().hasAttribute('disabled')).toBe(false);
	});

	it('shows a spinner and disables save while the dry-run is in flight', () => {
		render(DiffPreviewModal, { props: { ...baseProps(), preview: null, loading: true } });

		expect(screen.getByRole('status')).toBeTruthy();
		expect(saveButton().hasAttribute('disabled')).toBe(true);
	});

	it('disables save while loading even if a stale preview is still held', () => {
		render(DiffPreviewModal, { props: { ...baseProps(), loading: true } });

		expect(saveButton().hasAttribute('disabled')).toBe(true);
	});

	it('shows the ApiErrorView and disables save on error', () => {
		render(DiffPreviewModal, { props: { ...baseProps(), preview: null, error: ERROR } });

		expect(screen.getByText('write_conflict')).toBeTruthy();
		expect(screen.getByText('The ticket changed on disk.')).toBeTruthy();
		expect(screen.getByText('Reload and try again.')).toBeTruthy();
		expect(saveButton().hasAttribute('disabled')).toBe(true);
	});

	it('disables save when there is no preview at all', () => {
		render(DiffPreviewModal, { props: { ...baseProps(), preview: null } });

		expect(screen.getByText('No preview to review yet.')).toBeTruthy();
		expect(saveButton().hasAttribute('disabled')).toBe(true);
	});

	it('fires onConfirm when save is clicked', async () => {
		const props = baseProps();
		render(DiffPreviewModal, { props });

		await fireEvent.click(saveButton());

		expect(props.onConfirm).toHaveBeenCalledTimes(1);
		expect(props.onCancel).not.toHaveBeenCalled();
	});

	it('fires onCancel from the cancel button, the backdrop and Escape', async () => {
		const props = baseProps();
		render(DiffPreviewModal, { props });

		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
		await fireEvent.click(screen.getByRole('dialog').parentElement!.querySelector('button')!);
		await fireEvent.keyDown(window, { key: 'Escape' });

		expect(props.onCancel).toHaveBeenCalledTimes(3);
		expect(props.onConfirm).not.toHaveBeenCalled();
	});

	// The error view's action closes the dialog rather than reloading, so it is
	// labelled for what it does — a button reading "Reload" that only closes
	// would misdescribe the one screen that gates a write.
	it("labels the error view's only recovery affordance for what it does", async () => {
		const props = { ...baseProps(), preview: null, error: ERROR };
		render(DiffPreviewModal, { props });

		expect(screen.queryByRole('button', { name: 'Reload' })).toBeNull();
		await fireEvent.click(screen.getByRole('button', { name: 'Close' }));

		expect(props.onCancel).toHaveBeenCalledTimes(1);
	});

	// Inside a dialog already labelled by its own heading, the error message is
	// a section heading — an `<h1>` here would outrank that label.
	it('renders the nested error message below the dialog heading, not as an h1', () => {
		render(DiffPreviewModal, { props: { ...baseProps(), preview: null, error: ERROR } });

		expect(screen.queryByRole('heading', { level: 1 })).toBeNull();
		expect(screen.getByRole('heading', { name: 'The ticket changed on disk.' })).toBeTruthy();
	});

	it('wires the "nothing is written yet" line as the dialog description', () => {
		render(DiffPreviewModal, { props: baseProps() });

		const dialog = screen.getByRole('dialog');
		const describedBy = dialog.getAttribute('aria-describedby');
		expect(describedBy).toBe('diff-preview-description');
		expect(document.getElementById(describedBy!)?.textContent).toContain(
			'Nothing is written until you save.'
		);
	});

	it('cycles Tab within the dialog instead of letting it escape', async () => {
		const outside = document.createElement('button');
		document.body.appendChild(outside);

		render(DiffPreviewModal, { props: baseProps() });
		const region = screen.getByRole('region');

		saveButton().focus();
		await fireEvent.keyDown(saveButton(), { key: 'Tab' });
		expect(document.activeElement).toBe(region);

		await fireEvent.keyDown(region, { key: 'Tab', shiftKey: true });
		expect(document.activeElement).toBe(saveButton());

		expect(document.activeElement).not.toBe(outside);
		outside.remove();
	});

	// A disabled Save is out of the tab order, so the cycle has to close back
	// on the diff region — the first control — rather than walking out of the dialog.
	it('cycles Tab past the disabled save button while loading', async () => {
		render(DiffPreviewModal, { props: { ...baseProps(), preview: null, loading: true } });
		const cancel = screen.getByRole('button', { name: 'Cancel' });
		const region = screen.getByRole('region');

		cancel.focus();
		await fireEvent.keyDown(cancel, { key: 'Tab' });

		expect(document.activeElement).toBe(region);
	});

	it('ignores Escape while closed', async () => {
		const props = baseProps();
		render(DiffPreviewModal, { props: { ...props, open: false } });

		await fireEvent.keyDown(window, { key: 'Escape' });

		expect(props.onCancel).not.toHaveBeenCalled();
	});
});
