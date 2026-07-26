import { fireEvent, render, screen } from '@testing-library/svelte';
import { get } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';
import WriteTokenPrompt from '$lib/components/WriteTokenPrompt.svelte';
import { clearToken, writeToken } from '$lib/stores/writeToken';

// The prompt is `$app`-free and its only effect is the shared session store, so it
// unit-tests under jsdom against the real store. Reset the token between cases so
// jsdom's sessionStorage does not leak one test's token into the next.
afterEach(() => {
	clearToken();
});

function tokenField(): HTMLInputElement {
	return screen.getByLabelText('Write token') as HTMLInputElement;
}

function saveButton(): HTMLElement {
	return screen.getByRole('button', { name: 'Save token' });
}

describe('WriteTokenPrompt', () => {
	it('submitting a pasted token stores it, clears the field, and calls onSaved', async () => {
		const onSaved = vi.fn();
		render(WriteTokenPrompt, { props: { onSaved } });

		await fireEvent.input(tokenField(), { target: { value: 'tok-abc123' } });
		await fireEvent.click(saveButton());

		expect(get(writeToken)).toBe('tok-abc123');
		// The secret does not linger in the field once it is stored.
		expect(tokenField().value).toBe('');
		expect(onSaved).toHaveBeenCalledTimes(1);
	});

	it('trims the pasted value before storing it', async () => {
		render(WriteTokenPrompt, { props: {} });

		await fireEvent.input(tokenField(), { target: { value: '  tok-abc123  ' } });
		await fireEvent.click(saveButton());

		expect(get(writeToken)).toBe('tok-abc123');
	});

	it('keeps submit inert while the field is blank and stores nothing', async () => {
		const onSaved = vi.fn();
		const { container } = render(WriteTokenPrompt, { props: { onSaved } });

		expect(saveButton().hasAttribute('disabled')).toBe(true);

		// Whitespace only is still blank...
		await fireEvent.input(tokenField(), { target: { value: '   ' } });
		expect(saveButton().hasAttribute('disabled')).toBe(true);

		// ...and a submit that bypasses the disabled button (a stray Enter) is a no-op.
		const form = container.querySelector('form');
		if (!form) throw new Error('expected the prompt to render a form');
		await fireEvent.submit(form);

		expect(get(writeToken)).toBeNull();
		expect(onSaved).not.toHaveBeenCalled();
	});

	it('masks the token field and keeps it out of autocomplete', () => {
		render(WriteTokenPrompt, { props: {} });

		expect(tokenField().type).toBe('password');
		expect(tokenField().getAttribute('autocomplete')).toBe('off');
	});
});
