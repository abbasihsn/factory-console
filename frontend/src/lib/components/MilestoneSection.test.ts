import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import MilestoneSection from '$lib/components/MilestoneSection.svelte';
import type { RoadmapMilestone } from '$lib/api';

// MilestoneSection is presentational: render it with a supplied milestone and
// assert the name, each item's text, the id-link target + monospace class, and the
// run-state badge per item. Plain assertions (no container snapshot) sidestep the
// repo's trailing-whitespace hook, which would strip a whitespace-only sibling text
// node and desync a DOM snapshot (see TicketMiniRow.test.ts).
const milestone: RoadmapMilestone = {
	name: 'MVP',
	items: [
		{ text: 'Wire the API client', ticketId: 'T31', runState: 'merged' },
		{ text: 'Draft the empty states', ticketId: 'T32', runState: 'in_progress' },
		// Names no ticket, so there is no question to answer and no badge to show.
		{ text: 'Nice-to-have polish' }
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
		// The third item carries no ticketId, so only the two id-bearing items link.
		expect(screen.getAllByRole('link')).toHaveLength(2);
	});

	// REPLACES a test asserting ☑/☐/· glyphs read off a `done` flag. That flag came from
	// a `[x]` typed into ROADMAP.md — derived state in a committed file, stale the moment
	// a lane merged, and able to contradict the badge on the very ticket it linked to.
	it('shows each item the same run-state badge the ticket views show', () => {
		render(MilestoneSection, { props: { milestone } });

		// The labels are RunStateBadge's own, so sharing the component is what keeps the
		// roadmap and the ticket list from wording one state two ways.
		expect(screen.getByText('Merged')).toBeTruthy();
		expect(screen.getByText('In progress')).toBeTruthy();
	});

	it('renders no badge at all for an item that names no ticket', () => {
		render(MilestoneSection, { props: { milestone } });

		// NOT an `Unknown` pill: `unknown` means a source was asked and said nothing,
		// while this item has no ticket to ask about. Badging it would assert the factory
		// has never heard of a ticket that does not exist.
		expect(screen.queryByText('Unknown')).toBeNull();
	});

	it('renders no checkbox inputs — the roadmap is a view, never an editor', () => {
		const { container } = render(MilestoneSection, { props: { milestone } });

		expect(container.querySelector('input')).toBeNull();
	});
});
