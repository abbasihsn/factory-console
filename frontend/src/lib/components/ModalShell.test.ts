import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import { createRawSnippet, type ComponentProps } from 'svelte';
import ModalShell from '$lib/components/ModalShell.svelte';

// DiffPreviewModal's and ConfirmDialog's suites both mount with `open` already
// final, which only exercises focus-on-open at mount. The real consumers keep one
// shell mounted and TOGGLE `open`, so the closed -> open TRANSITION is what has to
// move focus — that is what this suite drives.
const body = createRawSnippet(() => ({ render: () => '<p>Body content</p>' }));
const actions = createRawSnippet(() => ({
	render: () => '<button type="button">Apply</button>'
}));

function props(
	overrides: Partial<ComponentProps<typeof ModalShell>> = {}
): ComponentProps<typeof ModalShell> {
	return {
		open: true,
		title: 'Review changes',
		body,
		actions,
		onCancel: vi.fn(),
		...overrides
	};
}

function cancelButton(): HTMLButtonElement {
	return screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement;
}

describe('ModalShell', () => {
	it('moves focus to cancel when opened AFTER mount, and Escape then cancels', async () => {
		const onCancel = vi.fn();
		const { rerender } = render(ModalShell, { props: props({ open: false, onCancel }) });

		expect(screen.queryByRole('dialog')).toBeNull();

		await rerender(props({ open: true, onCancel }));

		expect(screen.getByRole('dialog', { name: 'Review changes' })).toBeTruthy();
		// Focus lands on the LEAST destructive action, exactly as at mount — the
		// `bind:this` target has to be populated before the focus effect runs.
		expect(document.activeElement).toBe(cancelButton());

		await fireEvent.keyDown(window, { key: 'Escape' });

		expect(onCancel).toHaveBeenCalledTimes(1);
	});

	it('re-focuses cancel each time it is reopened, not just the first time', async () => {
		const onCancel = vi.fn();
		const { rerender } = render(ModalShell, { props: props({ open: true, onCancel }) });
		expect(document.activeElement).toBe(cancelButton());

		await rerender(props({ open: false, onCancel }));
		expect(screen.queryByRole('dialog')).toBeNull();

		await rerender(props({ open: true, onCancel }));

		expect(document.activeElement).toBe(cancelButton());
	});

	it('renders the body and action snippets its callers supply', () => {
		render(ModalShell, { props: props() });

		expect(screen.getByText('Body content')).toBeTruthy();
		expect(screen.getByRole('button', { name: 'Apply' })).toBeTruthy();
	});
});
