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
		context: 'Why this ticket exists.',
		approach: 'Create the module, then wire it up.',
		criticalFiles: 'src/a.ts',
		interfaceData: 'N/A',
		verificationCommands: 'pnpm test',
		verificationNotes: '',
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

	it('a valid create fires onSubmit with the full TicketFormValues', async () => {
		const onSubmit = vi.fn();
		render(TicketForm, {
			props: {
				mode: 'create',
				initial: initial({
					id: 'T99',
					title: 'Build it',
					dependsOn: 'T67\nT29',
					provides: 'the form',
					criticalFiles: 'src/lib/components/TicketForm.svelte',
					verificationCommands: 'pnpm test\npnpm check'
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
			context: 'Why this ticket exists.',
			approach: 'Create the module, then wire it up.',
			criticalFiles: 'src/lib/components/TicketForm.svelte',
			interfaceData: 'N/A',
			verificationCommands: 'pnpm test\npnpm check',
			verificationNotes: ''
		});
	});

	it('reflects edited fields in the emitted onSubmit values', async () => {
		const onSubmit = vi.fn();
		render(TicketForm, {
			props: { mode: 'create', initial: initial({ id: 'T99', title: 'Old' }), onSubmit }
		});

		await fireEvent.input(screen.getByLabelText('Title'), { target: { value: 'New title' } });
		await fireEvent.input(screen.getByLabelText('Depends on'), { target: { value: 'T67\nT29' } });
		await fireEvent.input(screen.getByLabelText('Context'), { target: { value: 'Edited why.' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Create ticket' }));

		expect(onSubmit).toHaveBeenCalledWith(
			expect.objectContaining({
				id: 'T99',
				title: 'New title',
				dependsOn: 'T67\nT29',
				context: 'Edited why.'
			})
		);
	});

	it('a valid edit fires onSubmit with the id fixed from initial', async () => {
		const onSubmit = vi.fn();
		render(TicketForm, {
			props: {
				mode: 'edit',
				initial: initial({ id: 'T68', title: 'Editable title' }),
				onSubmit
			}
		});

		await fireEvent.input(screen.getByLabelText('Title'), { target: { value: 'Updated title' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

		expect(onSubmit).toHaveBeenCalledTimes(1);
		expect(onSubmit).toHaveBeenCalledWith(
			expect.objectContaining({ id: 'T68', title: 'Updated title' })
		);
	});

	// The v3 content surface, asserted as a SET: five fields plus the optional notes, and
	// no free-text body. A ticket has no free-text body in v3, and the schema's
	// `additionalProperties: false` leaves nowhere to put one — so an editor that offered
	// it would collect prose the server must refuse or silently drop.
	it('renders the five content fields and no markdown body editor', () => {
		render(TicketForm, { props: { mode: 'create', initial: initial(), onSubmit: vi.fn() } });

		for (const label of [
			'Context',
			'Staged approach',
			'Critical files',
			'Interface and data',
			'Verification commands',
			'Verification notes'
		]) {
			expect(screen.getByLabelText(label)).toBeTruthy();
		}
		expect(document.querySelector('[data-testid="markdown-editor"]')).toBeNull();
		expect(screen.queryByLabelText('Ticket body')).toBeNull();
	});

	it('seeds the content fields from initial rather than starting them blank', () => {
		render(TicketForm, {
			props: {
				mode: 'edit',
				initial: initial({ context: 'Seeded context.', verificationNotes: 'needs DATABASE_URL' }),
				onSubmit: vi.fn()
			}
		});

		expect((screen.getByLabelText('Context') as HTMLTextAreaElement).value).toBe('Seeded context.');
		expect((screen.getByLabelText('Verification notes') as HTMLTextAreaElement).value).toBe(
			'needs DATABASE_URL'
		);
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
		expect(screen.getByLabelText('Critical files').tagName).toBe('TEXTAREA');
		expect(screen.getByLabelText('Verification commands').tagName).toBe('TEXTAREA');
	});

	it('disabled makes every field inert and the submit button disabled', () => {
		const onSubmit = vi.fn();
		render(TicketForm, {
			props: { mode: 'create', initial: initial(), disabled: true, onSubmit }
		});

		for (const label of [
			'Ticket id',
			'Title',
			'Depends on',
			'Provides',
			'Context',
			'Staged approach',
			'Critical files',
			'Interface and data',
			'Verification commands',
			'Verification notes'
		]) {
			expect(screen.getByLabelText(label).hasAttribute('disabled')).toBe(true);
		}
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

	// A blank required content field is not a silent no-op: it blocks submit, exactly as a
	// malformed id does. Otherwise the first the user hears of it is a 422 after a dry-run.
	it('blocks submit while a required content field is blank', async () => {
		const onSubmit = vi.fn();
		render(TicketForm, {
			props: { mode: 'create', initial: initial({ context: '' }), onSubmit }
		});

		expect(screen.getByRole('button', { name: 'Create ticket' }).hasAttribute('disabled')).toBe(
			true
		);

		await fireEvent.input(screen.getByLabelText('Context'), { target: { value: 'Now filled.' } });

		await waitFor(() =>
			expect(screen.getByRole('button', { name: 'Create ticket' }).hasAttribute('disabled')).toBe(
				false
			)
		);
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
