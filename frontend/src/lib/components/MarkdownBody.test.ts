import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import MarkdownBody from '$lib/components/MarkdownBody.svelte';

// Proves the `@html` trust boundary injects the server-sanitized markup as
// real DOM (elements are queryable, not escaped text).
describe('MarkdownBody', () => {
	it('injects server-rendered HTML as real DOM via @html', () => {
		const html = '<h1>Title</h1><p>Body <strong>text</strong></p>';
		const { container } = render(MarkdownBody, { props: { html } });

		expect(container.innerHTML).toContain('<h1>Title</h1>');
		expect(container.innerHTML).toContain('<strong>text</strong>');

		// Real element nodes, not escaped text.
		expect(container.querySelector('h1')?.textContent).toBe('Title');
		expect(container.querySelector('strong')?.textContent).toBe('text');
	});

	it('wraps the body in a prose container', () => {
		const { container } = render(MarkdownBody, { props: { html: '<p>x</p>' } });
		expect(container.querySelector('div.prose')).toBeTruthy();
	});
});
