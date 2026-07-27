import { fireEvent, render, screen } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import { describe, expect, it, vi } from 'vitest';
import ModalShell from '$lib/components/ModalShell.svelte';

// ModalShell owns the backdrop, Escape and focus mechanics for EVERY dialog, so
// it is tested directly rather than only through ConfirmDialog / DiffPreviewModal.
// Those callers all render at least one focusable control, which leaves the
// empty-panel branch of the focus trap — the case where Tab would walk straight
// out of an `aria-modal` dialog — unreachable from their tests.
function body(html: string) {
	return createRawSnippet(() => ({ render: () => html }));
}

const TWO_BUTTONS = body(
	'<div><button type="button">First</button><button type="button">Last</button></div>'
);
const NOTHING_FOCUSABLE = body('<p>Read-only notice.</p>');

function baseProps(children = TWO_BUTTONS) {
	return {
		open: true,
		labelledBy: 'shell-title',
		panelClass: 'w-full max-w-md p-4',
		onCancel: vi.fn(),
		children
	};
}

describe('ModalShell', () => {
	it('renders nothing while closed', () => {
		render(ModalShell, { props: { ...baseProps(), open: false } });

		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('wires the panel as a named modal dialog and applies the caller panel class', () => {
		render(ModalShell, { props: { ...baseProps(), describedBy: 'shell-description' } });

		const dialog = screen.getByRole('dialog');
		expect(dialog.getAttribute('aria-modal')).toBe('true');
		expect(dialog.getAttribute('aria-labelledby')).toBe('shell-title');
		expect(dialog.getAttribute('aria-describedby')).toBe('shell-description');
		expect(dialog.className).toContain('max-w-md');
	});

	// The backdrop dismisses on click but must not be reachable by Tab or exposed
	// to AT — `aria-modal` on the panel promises everything outside it is gone.
	it('dismisses from the backdrop while keeping it out of the tab order and AT', async () => {
		const props = baseProps();
		render(ModalShell, { props });
		const backdrop = screen.getByRole('dialog').parentElement!.querySelector('button')!;

		expect(backdrop.getAttribute('tabindex')).toBe('-1');
		expect(backdrop.getAttribute('aria-hidden')).toBe('true');

		await fireEvent.click(backdrop);
		expect(props.onCancel).toHaveBeenCalledTimes(1);
	});

	it('reports Escape while open and ignores it while closed', async () => {
		const props = baseProps();
		const { rerender } = render(ModalShell, { props });

		await fireEvent.keyDown(window, { key: 'Escape' });
		expect(props.onCancel).toHaveBeenCalledTimes(1);

		await rerender({ ...props, open: false });
		await fireEvent.keyDown(window, { key: 'Escape' });
		expect(props.onCancel).toHaveBeenCalledTimes(1);
	});

	// Keys are only ours while focus is inside this dialog — that is also what
	// makes stacked dialogs work, since only the topmost one holds focus.
	it('ignores keys pressed while focus is outside the dialog', async () => {
		const outside = document.createElement('button');
		document.body.appendChild(outside);

		const props = baseProps();
		render(ModalShell, { props });
		outside.focus();

		await fireEvent.keyDown(outside, { key: 'Escape' });

		expect(props.onCancel).not.toHaveBeenCalled();
		outside.remove();
	});

	it('moves focus to the panel on open and hands it back to the opener on close', async () => {
		const opener = document.createElement('button');
		document.body.appendChild(opener);
		opener.focus();

		const { rerender } = render(ModalShell, { props: baseProps() });
		expect(document.activeElement).toBe(screen.getByRole('dialog'));

		await rerender({ ...baseProps(), open: false });

		expect(document.activeElement).toBe(opener);
		opener.remove();
	});

	it('cycles Tab within the panel in both directions', async () => {
		render(ModalShell, { props: baseProps() });
		const first = screen.getByRole('button', { name: 'First' });
		const last = screen.getByRole('button', { name: 'Last' });

		// Forward past the last control wraps to the first.
		last.focus();
		await fireEvent.keyDown(last, { key: 'Tab' });
		expect(document.activeElement).toBe(first);

		// Backward from the first control wraps to the last.
		await fireEvent.keyDown(first, { key: 'Tab', shiftKey: true });
		expect(document.activeElement).toBe(last);
	});

	// Shift+Tab from the panel itself is the same wrap: the panel is where focus
	// lands on open, and it sits ahead of its own contents.
	it('treats the panel as the start of the cycle on Shift+Tab', async () => {
		render(ModalShell, { props: baseProps() });
		const panel = screen.getByRole('dialog');

		panel.focus();
		await fireEvent.keyDown(panel, { key: 'Tab', shiftKey: true });

		expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Last' }));
	});

	// The branch every caller-level test misses: with nothing focusable inside,
	// the default Tab would leave the dialog entirely, so it is swallowed and
	// focus stays put.
	it('holds focus on the panel when it contains nothing focusable', async () => {
		const outside = document.createElement('button');
		document.body.appendChild(outside);

		render(ModalShell, { props: baseProps(NOTHING_FOCUSABLE) });
		const panel = screen.getByRole('dialog');
		panel.focus();

		const forward = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
		await fireEvent(panel, forward);
		expect(forward.defaultPrevented).toBe(true);

		const backward = new KeyboardEvent('keydown', {
			key: 'Tab',
			shiftKey: true,
			bubbles: true,
			cancelable: true
		});
		await fireEvent(panel, backward);
		expect(backward.defaultPrevented).toBe(true);

		expect(document.activeElement).toBe(panel);
		expect(document.activeElement).not.toBe(outside);
		outside.remove();
	});

	it('leaves keys other than Escape and Tab alone', async () => {
		const props = baseProps();
		render(ModalShell, { props });
		const first = screen.getByRole('button', { name: 'First' });
		first.focus();

		await fireEvent.keyDown(first, { key: 'Enter' });

		expect(props.onCancel).not.toHaveBeenCalled();
		expect(document.activeElement).toBe(first);
	});
});
