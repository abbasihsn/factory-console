import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import MarkdownBody from '$lib/components/MarkdownBody.svelte';

// MarkdownBody is the SPA's sole `@html` boundary: assert the supplied server
// HTML is injected as live DOM (real `<strong>`, queryable text) rather than
// escaped into literal text.
describe('MarkdownBody', () => {
	it('injects the supplied HTML as live DOM elements', () => {
		const html = '<p>Hello <strong>world</strong></p>';
		const { container } = render(MarkdownBody, { props: { html } });

		// `@html` produced real elements, not escaped text.
		expect(container.querySelector('strong')).toBeTruthy();
		expect(screen.getByText('world')).toBeTruthy();
		expect(container.innerHTML).toContain(html);
	});
});
