import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'svelte';
import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';

/** Baseline props: an open delete confirmation. */
function props(
	overrides: Partial<ComponentProps<typeof ConfirmDialog>> = {}
): ComponentProps<typeof ConfirmDialog> {
	return {
		open: true,
		title: 'Delete ticket T69?',
		message: 'This removes the ticket file and its manifest entry.',
		confirmLabel: 'Delete ticket',
		onConfirm: vi.fn(),
		onCancel: vi.fn(),
		...overrides
	};
}

describe('ConfirmDialog', () => {
	it('renders nothing until it is opened', () => {
		render(ConfirmDialog, { props: props({ open: false }) });

		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('shows the title, message and confirm label, named for assistive tech', () => {
		render(ConfirmDialog, { props: props() });

		const dialog = screen.getByRole('dialog', { name: 'Delete ticket T69?' });
		expect(dialog.getAttribute('aria-modal')).toBe('true');
		expect(screen.getByText('This removes the ticket file and its manifest entry.')).toBeTruthy();
		expect(screen.getByRole('button', { name: 'Delete ticket' })).toBeTruthy();
	});

	it('fires onConfirm once when the confirm button is clicked', async () => {
		const onConfirm = vi.fn();
		const onCancel = vi.fn();
		render(ConfirmDialog, { props: props({ onConfirm, onCancel }) });

		await fireEvent.click(screen.getByRole('button', { name: 'Delete ticket' }));

		expect(onConfirm).toHaveBeenCalledTimes(1);
		expect(onCancel).not.toHaveBeenCalled();
	});

	it('fires onCancel from the cancel button, Escape, and a backdrop click', async () => {
		const onConfirm = vi.fn();
		const onCancel = vi.fn();
		const { container } = render(ConfirmDialog, { props: props({ onConfirm, onCancel }) });

		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
		await fireEvent.keyDown(window, { key: 'Escape' });
		const backdrop = container.querySelector('[data-testid="modal-backdrop"]');
		if (!backdrop) throw new Error('expected the dialog to render a backdrop');
		await fireEvent.click(backdrop);

		expect(onCancel).toHaveBeenCalledTimes(3);
		// No dismissal path may perform the destructive action.
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it('ignores Escape once closed', async () => {
		const onCancel = vi.fn();
		render(ConfirmDialog, { props: props({ open: false, onCancel }) });

		await fireEvent.keyDown(window, { key: 'Escape' });

		expect(onCancel).not.toHaveBeenCalled();
	});

	it('opens with focus on cancel, so a stray Enter cannot confirm a delete', () => {
		render(ConfirmDialog, { props: props() });

		expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }));
	});

	it('styles the confirm button as destructive only when danger is set', () => {
		const { unmount } = render(ConfirmDialog, { props: props() });
		expect(screen.getByRole('button', { name: 'Delete ticket' }).className).not.toContain(
			'bg-danger'
		);
		unmount();

		render(ConfirmDialog, { props: props({ danger: true }) });
		expect(screen.getByRole('button', { name: 'Delete ticket' }).className).toContain('bg-danger');
	});
});
