import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import TicketForm from '$lib/components/TicketForm.svelte';
import type { TicketFormValues } from '$lib/forms/ticketForm';

/** A fully-valid set of initial values; individual tests override single fields. */
function initial(overrides: Partial<TicketFormValues> = {}): TicketFormValues {
	return {
		id: 'T68',
		title: 'A valid title',
		dependsOn: '',
		provides: '',
		files: '',
		...overrides
	};
}

/** The id-pattern error text, kept in sync with `validateTicketForm`. */
const ID_ERROR = 'Ticket id may only contain letters, digits, and _ . - characters.';

describe('TicketForm', () => {
	it('renders in create mode with an editable id and a "Create ticket" submit', () => {
		render(TicketForm, {
			props: { mode: 'create', initial: initial(), onSubmit: vi.fn() }
		});

		expect(screen.getByRole('button', { name: 'Create ticket' })).toBeTruthy();
		// id is editable (not read-only) in create mode.
		expect(screen.getByLabelText('Ticket id').hasAttribute('readonly')).toBe(false);
	});

	it('renders in edit mode with a read-only id and a "Save changes" submit', () => {
		render(TicketForm, {
			props: { mode: 'edit', initial: initial(), onSubmit: vi.fn() }
		});

		expect(screen.getByRole('button', { name: 'Save changes' })).toBeTruthy();
		expect(screen.getByLabelText('Ticket id').hasAttribute('readonly')).toBe(true);
	});

	it('typing an invalid id disables submit and shows the id error text', async () => {
		render(TicketForm, {
			props: { mode: 'create', initial: initial(), onSubmit: vi.fn() }
		});

		await fireEvent.input(screen.getByLabelText('Ticket id'), { target: { value: 'a b' } });

		expect(screen.getByRole('button', { name: 'Create ticket' }).hasAttribute('disabled')).toBe(
			true
		);
		expect(screen.getByText(ID_ERROR)).toBeTruthy();
	});

	it('a valid create fires onSubmit with the full TicketFormValues (incl. body and lists)', async () => {
		const onSubmit = vi.fn();
		render(TicketForm, {
			props: {
				mode: 'create',
				// Seed body + lists via `initial` so they round-trip into onSubmit
				// without needing to drive CodeMirror under jsdom.
				initial: initial({
					id: 'T99',
					title: 'Build it',
					dependsOn: 'T67\nT29',
					provides: 'the form',
					files: 'src/lib/components/TicketForm.svelte',
					body: '# Heading\n\nSome body text.'
				}),
				onSubmit
			}
		});

		await fireEvent.click(screen.getByRole('button', { name: 'Create ticket' }));

		expect(onSubmit).toHaveBeenCalledTimes(1);
		expect(onSubmit).toHaveBeenCalledWith({
			id: 'T99',
			title: 'Build it',
			dependsOn: 'T67\nT29',
			provides: 'the form',
			files: 'src/lib/components/TicketForm.svelte',
			body: '# Heading\n\nSome body text.'
		});
	});

	it('reflects edited fields in the emitted onSubmit values', async () => {
		const onSubmit = vi.fn();
		render(TicketForm, {
			props: { mode: 'create', initial: initial({ id: 'T99', title: 'Old' }), onSubmit }
		});

		await fireEvent.input(screen.getByLabelText('Title'), { target: { value: 'New title' } });
		await fireEvent.input(screen.getByLabelText('Depends on'), { target: { value: 'T67\nT29' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Create ticket' }));

		expect(onSubmit).toHaveBeenCalledWith({
			id: 'T99',
			title: 'New title',
			dependsOn: 'T67\nT29',
			provides: '',
			files: '',
			body: ''
		});
	});

	it('a valid edit fires onSubmit with the id fixed from initial', async () => {
		const onSubmit = vi.fn();
		render(TicketForm, {
			props: {
				mode: 'edit',
				initial: initial({ id: 'T68', title: 'Editable title', body: 'body text' }),
				onSubmit
			}
		});

		await fireEvent.input(screen.getByLabelText('Title'), { target: { value: 'Updated title' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

		expect(onSubmit).toHaveBeenCalledTimes(1);
		expect(onSubmit).toHaveBeenCalledWith({
			id: 'T68',
			title: 'Updated title',
			dependsOn: '',
			provides: '',
			files: '',
			body: 'body text'
		});
	});

	it('edits provides in a single-line input, not a newline textarea', () => {
		// `provides` is a SCALAR on the wire (`TicketDraft.provides: string`), so a
		// textarea would invite a multi-entry value the server stores verbatim and the
		// read model hands back collapsed into one element. The sibling list fields stay
		// textareas. Asserted because `disabled` alone passes for either element, so a
		// revert to a textarea would otherwise stay green.
		render(TicketForm, { props: { mode: 'create', initial: initial(), onSubmit: vi.fn() } });

		expect(screen.getByLabelText('Provides').tagName).toBe('INPUT');
		expect(screen.getByLabelText('Depends on').tagName).toBe('TEXTAREA');
		expect(screen.getByLabelText('Files').tagName).toBe('TEXTAREA');
	});

	it('disabled makes every field inert and the submit button disabled', () => {
		const onSubmit = vi.fn();
		render(TicketForm, {
			props: { mode: 'create', initial: initial(), disabled: true, onSubmit }
		});

		expect(screen.getByLabelText('Ticket id').hasAttribute('disabled')).toBe(true);
		expect(screen.getByLabelText('Title').hasAttribute('disabled')).toBe(true);
		expect(screen.getByLabelText('Depends on').hasAttribute('disabled')).toBe(true);
		expect(screen.getByLabelText('Provides').hasAttribute('disabled')).toBe(true);
		expect(screen.getByLabelText('Files').hasAttribute('disabled')).toBe(true);
		expect(screen.getByRole('button', { name: 'Create ticket' }).hasAttribute('disabled')).toBe(
			true
		);
	});

	it('does not fire onSubmit while disabled', async () => {
		const onSubmit = vi.fn();
		render(TicketForm, {
			props: { mode: 'create', initial: initial(), disabled: true, onSubmit }
		});

		// The form itself can still receive a submit event even with the button
		// disabled; the guard must swallow it.
		const form = screen.getByRole('button', { name: 'Create ticket' }).closest('form')!;
		await fireEvent.submit(form);

		expect(onSubmit).not.toHaveBeenCalled();
	});

	it('makes the markdown body read-only when disabled', async () => {
		render(TicketForm, {
			props: { mode: 'create', initial: initial(), disabled: true, onSubmit: vi.fn() }
		});

		await waitFor(() =>
			expect(
				document.querySelector('[data-testid="markdown-editor"] [contenteditable]')
			).toBeTruthy()
		);
		expect(
			document
				.querySelector('[data-testid="markdown-editor"] [contenteditable]')
				?.getAttribute('contenteditable')
		).toBe('false');
	});

	it('calls onValidityChange with the initial validity on mount, then again when it flips', async () => {
		const onValidityChange = vi.fn();
		render(TicketForm, {
			props: {
				mode: 'create',
				// Start invalid: id + title both blank in create mode.
				initial: initial({ id: '', title: '' }),
				onSubmit: vi.fn(),
				onValidityChange
			}
		});

		// Emits the starting state once on mount.
		await waitFor(() => expect(onValidityChange).toHaveBeenCalledWith(false));
		expect(onValidityChange).toHaveBeenCalledTimes(1);

		// Fill the required fields -> validity flips to true.
		await fireEvent.input(screen.getByLabelText('Ticket id'), { target: { value: 'T99' } });
		await fireEvent.input(screen.getByLabelText('Title'), { target: { value: 'Now valid' } });

		await waitFor(() => expect(onValidityChange).toHaveBeenLastCalledWith(true));
		// One mount emit + one flip; no per-render spam.
		expect(onValidityChange).toHaveBeenCalledTimes(2);
	});
});
