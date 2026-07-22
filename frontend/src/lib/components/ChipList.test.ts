import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import ChipList from '$lib/components/ChipList.svelte';

// ChipList is presentational: render a mix of linked + plain items and assert that
// items with an `href` become anchors pointing at it, plain items become spans (not
// links), and every label renders.
describe('ChipList', () => {
	it('renders href items as anchors and plain items as spans', () => {
		render(ChipList, {
			props: {
				items: [{ label: 'T30', href: '/tickets/T30' }, { label: 'Some capability' }]
			}
		});

		const link = screen.getByRole('link', { name: 'T30' });
		expect(link.tagName).toBe('A');
		expect(link.getAttribute('href')).toBe('/tickets/T30');

		// The plain item has no href → it is not a link, just a span.
		expect(screen.queryByRole('link', { name: 'Some capability' })).toBeNull();
		const plain = screen.getByText('Some capability');
		expect(plain.tagName).toBe('SPAN');
	});

	it('renders every item label', () => {
		render(ChipList, {
			props: {
				items: [{ label: 'alpha', href: '/tickets/alpha' }, { label: 'beta' }]
			}
		});

		expect(screen.getByText('alpha')).toBeTruthy();
		expect(screen.getByText('beta')).toBeTruthy();
	});
});
