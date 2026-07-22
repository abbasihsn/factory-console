import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import TopBar from '$lib/components/TopBar.svelte';

// The layout composes the presentational TopBar with `invalidateAll`; the smoke
// test targets TopBar with a supplied project prop so no router/backend is
// needed (see the ticket's testability note).
describe('TopBar (layout top bar)', () => {
	it('renders the app name and the supplied project root path', () => {
		const rootPath = '/home/dev/factory-console';
		render(TopBar, { props: { project: { rootPath } } });

		expect(screen.getByText('Factory Console')).toBeTruthy();

		const rootEl = screen.getByText(rootPath);
		expect(rootEl).toBeTruthy();
		// Full path is preserved in the title attribute (the text is truncated).
		expect(rootEl.getAttribute('title')).toBe(rootPath);
	});
});
