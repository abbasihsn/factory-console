import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';

// ConfirmDialog is presentational: it only reports the decision, so every case
// asserts which callback fired for a given interaction.
function baseProps() {
	return {
		open: true,
		title: 'Delete T42?',
		message: 'This removes the ticket file from disk.',
		confirmLabel: 'Delete',
		onConfirm: vi.fn(),
		onCancel: vi.fn()
	};
}

describe('ConfirmDialog', () => {
	it('renders nothing while closed', () => {
		render(ConfirmDialog, { props: { ...baseProps(), open: false } });

		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('renders the title, message and confirm label in a modal dialog', () => {
		render(ConfirmDialog, { props: baseProps() });

		const dialog = screen.getByRole('dialog');
		expect(dialog.getAttribute('aria-modal')).toBe('true');
		expect(screen.getByRole('heading', { name: 'Delete T42?' })).toBeTruthy();
		expect(screen.getByText('This removes the ticket file from disk.')).toBeTruthy();
		expect(screen.getByRole('button', { name: 'Delete' })).toBeTruthy();
	});

	it('fires onConfirm when the confirm button is clicked', async () => {
		const props = baseProps();
		render(ConfirmDialog, { props });

		await fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

		expect(props.onConfirm).toHaveBeenCalledTimes(1);
		expect(props.onCancel).not.toHaveBeenCalled();
	});

	it('fires onCancel from the cancel button and from the backdrop', async () => {
		const props = baseProps();
		render(ConfirmDialog, { props });

		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Dismiss dialog' }));

		expect(props.onCancel).toHaveBeenCalledTimes(2);
		expect(props.onConfirm).not.toHaveBeenCalled();
	});

	it('fires onCancel on Escape, and never confirms by keyboard accident', async () => {
		const props = baseProps();
		render(ConfirmDialog, { props });

		await fireEvent.keyDown(window, { key: 'Escape' });
		// Any other key is ignored.
		await fireEvent.keyDown(window, { key: 'Enter' });

		expect(props.onCancel).toHaveBeenCalledTimes(1);
		expect(props.onConfirm).not.toHaveBeenCalled();
	});

	it('ignores Escape while closed', async () => {
		const props = baseProps();
		render(ConfirmDialog, { props: { ...props, open: false } });

		await fireEvent.keyDown(window, { key: 'Escape' });

		expect(props.onCancel).not.toHaveBeenCalled();
	});

	it('styles the confirm button as destructive only when danger is set', () => {
		const { unmount } = render(ConfirmDialog, { props: baseProps() });
		expect(screen.getByRole('button', { name: 'Delete' }).className).toContain('bg-accent');
		unmount();

		render(ConfirmDialog, { props: { ...baseProps(), danger: true } });
		expect(screen.getByRole('button', { name: 'Delete' }).className).toContain('bg-danger');
	});
});
