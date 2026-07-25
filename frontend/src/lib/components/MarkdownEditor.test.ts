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
});
