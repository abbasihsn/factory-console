import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import SourcesBanner from '$lib/components/SourcesBanner.svelte';

const RESULTS = '/home/dev/factory-console/.factory/results';
const RECEIPTS = '/home/dev/factory-console/.factory/receipts';

// Svelte's markup wraps long prose across lines; collapse whitespace before
// matching so an assertion is about the sentence, not about its indentation.
function normalized(node: Element | null | undefined): string {
	return (node?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

describe('SourcesBanner', () => {
	it('explains the no-run-data case and names both probed directories', () => {
		render(SourcesBanner, {
			props: { allAbsent: true, resultsPath: RESULTS, receiptsPath: RECEIPTS }
		});

		const banner = screen.getByTestId('no-run-data');
		expect(normalized(banner)).toMatch(/No factory run data in this project\./);
		// Where the console looked — the only thing an operator can act on.
		expect(screen.getByText(RESULTS)).toBeTruthy();
		expect(screen.getByText(RECEIPTS)).toBeTruthy();
		// And why it is empty, so a fresh clone does not read as a failed factory.
		expect(normalized(banner)).toMatch(/machine-local and gitignored/i);
	});

	it('renders nothing at all when the project has some run data', () => {
		const { container } = render(SourcesBanner, {
			props: { allAbsent: false, resultsPath: RESULTS, receiptsPath: RECEIPTS }
		});

		expect(screen.queryByTestId('no-run-data')).toBeNull();
		expect(container.textContent?.trim()).toBe('');
	});

	it('falls back to the relative artifact directories when no path is known', () => {
		// An empty manifest has no record to read a probed path off, and the sentence
		// still has to be able to say where the console looks.
		render(SourcesBanner, { props: { allAbsent: true } });

		expect(screen.getByText('.factory/results')).toBeTruthy();
		expect(screen.getByText('.factory/receipts')).toBeTruthy();
	});
});
