import { render, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { EditorView } from '@codemirror/view';
import MarkdownEditor from '$lib/components/MarkdownEditor.svelte';

/** Grab the test-only EditorView hook the component attaches to its host node. */
async function getView(container: HTMLElement): Promise<EditorView> {
	const host = container.querySelector('[data-testid="markdown-editor"]') as
		(HTMLDivElement & { __view?: EditorView }) | null;
	await waitFor(() => expect(host?.__view).toBeTruthy());
	return host!.__view!;
}

describe('MarkdownEditor', () => {
	it('renders the initial value into the editor DOM', async () => {
		const { container } = render(MarkdownEditor, {
			props: { value: '# Hello world', onChange: vi.fn() }
		});

		await getView(container);
		const content = container.querySelector('.cm-content');
		expect(content?.textContent).toContain('# Hello world');
	});

	it('fires onChange with the new doc string on a programmatic edit', async () => {
		const onChange = vi.fn();
		const { container } = render(MarkdownEditor, {
			props: { value: 'original', onChange }
		});

		const view = await getView(container);
		view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: 'edited text' } });

		expect(onChange).toHaveBeenCalledWith('edited text');
	});

	it('applies aria-label to the contenteditable surface', async () => {
		const { container } = render(MarkdownEditor, {
			props: { value: '', onChange: vi.fn(), ariaLabel: 'Ticket body' }
		});

		await getView(container);
		const content = container.querySelector('.cm-content');
		expect(content?.getAttribute('aria-label')).toBe('Ticket body');
	});

	it('makes the editable surface non-editable when readOnly is true', async () => {
		const { container } = render(MarkdownEditor, {
			props: { value: 'locked', onChange: vi.fn(), readOnly: true }
		});

		await getView(container);
		const content = container.querySelector('[contenteditable]');
		expect(content?.getAttribute('contenteditable')).toBe('false');
	});

	it('is editable by default (readOnly defaults to false)', async () => {
		const { container } = render(MarkdownEditor, {
			props: { value: 'editable', onChange: vi.fn() }
		});

		await getView(container);
		const content = container.querySelector('[contenteditable]');
		expect(content?.getAttribute('contenteditable')).toBe('true');
	});

	it('reconciles an external value change into the doc WITHOUT echoing onChange', async () => {
		const onChange = vi.fn();
		const { container, rerender } = render(MarkdownEditor, {
			props: { value: 'first', onChange }
		});

		const view = await getView(container);
		expect(view.state.doc.toString()).toBe('first');

		// A programmatic `value` change (e.g. a form reset) reconciles into the doc...
		await rerender({ value: 'second', onChange });
		await waitFor(() => expect(view.state.doc.toString()).toBe('second'));

		// ...but is NOT a user edit, so onChange must not fire for it.
		expect(onChange).not.toHaveBeenCalled();
	});

	it('reconfigures readOnly through the compartment when the prop toggles after mount', async () => {
		const { container, rerender } = render(MarkdownEditor, {
			props: { value: 'x', onChange: vi.fn(), readOnly: false }
		});

		await getView(container);
		expect(container.querySelector('[contenteditable]')?.getAttribute('contenteditable')).toBe(
			'true'
		);

		await rerender({ value: 'x', onChange: vi.fn(), readOnly: true });
		await waitFor(() =>
			expect(container.querySelector('[contenteditable]')?.getAttribute('contenteditable')).toBe(
				'false'
			)
		);

		await rerender({ value: 'x', onChange: vi.fn(), readOnly: false });
		await waitFor(() =>
			expect(container.querySelector('[contenteditable]')?.getAttribute('contenteditable')).toBe(
				'true'
			)
		);
	});
});
