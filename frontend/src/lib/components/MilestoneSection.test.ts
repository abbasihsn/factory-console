import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import MilestoneSection from '$lib/components/MilestoneSection.svelte';
import type { RoadmapMilestone } from '$lib/api';

// MilestoneSection is presentational: render it with a supplied milestone and
// assert the name, each item's text, the id-link target + monospace class, and the
// read-only checkbox glyph per done state. Plain assertions (no container snapshot)
// sidestep the repo's trailing-whitespace hook, which would strip a whitespace-only
// sibling text node and desync a DOM snapshot (see TicketMiniRow.test.ts).
const milestone: RoadmapMilestone = {
	name: 'MVP',
	items: [
		{ text: 'Wire the API client', ticketId: 'T31', done: true },
		{ text: 'Draft the empty states', done: false },
		{ text: 'Nice-to-have polish', done: null }
	]
};

describe('MilestoneSection', () => {
	it('renders the milestone name and each item text', () => {
		render(MilestoneSection, { props: { milestone } });

		expect(screen.getByRole('heading', { name: 'MVP' })).toBeTruthy();
		expect(screen.getByText('Wire the API client')).toBeTruthy();
		expect(screen.getByText('Draft the empty states')).toBeTruthy();
		expect(screen.getByText('Nice-to-have polish')).toBeTruthy();
	});

	it('links the monospace id to the ticket detail route only when ticketId is present', () => {
		render(MilestoneSection, { props: { milestone } });

		const idLink = screen.getByRole('link', { name: 'T31' });
		expect(idLink.getAttribute('href')).toBe('/tickets/T31');
		expect(idLink.className).toContain('font-mono');
		// The done:false / done:null items carry no ticketId, so T31 is the only link.
		expect(screen.getAllByRole('link')).toHaveLength(1);
	});

	it('reflects the checkbox state in read-only glyphs, not inputs', () => {
		const { container } = render(MilestoneSection, { props: { milestone } });

		// No real checkbox inputs — the state is a glyph.
		expect(container.querySelector('input')).toBeNull();
		expect(screen.getByText('☑')).toBeTruthy();
		expect(screen.getByText('☐')).toBeTruthy();
		expect(screen.getByText('·')).toBeTruthy();
		expect(screen.getByLabelText('done')).toBeTruthy();
		expect(screen.getByLabelText('not done')).toBeTruthy();
		expect(screen.getByLabelText('no checkbox state')).toBeTruthy();
	});
});
