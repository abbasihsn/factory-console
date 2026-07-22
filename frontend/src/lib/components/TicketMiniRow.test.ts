import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import TicketMiniRow from '$lib/components/TicketMiniRow.svelte';
import type { TicketSummary } from '$lib/api';

// TicketMiniRow is presentational: render it with a supplied ticket and assert the
// id link target + monospace class, the title, and both badges. Plain assertions
// (no container snapshot) sidestep the repo's trailing-whitespace hook, which would
// strip a whitespace-only sibling text node and desync a DOM snapshot (see
// StatusBadge.test.ts / TicketRow.test.ts).
const ticket: TicketSummary = {
	id: 'T31',
	title: 'Ticket detail route',
	status: 'in_progress',
	track: 'frontend',
	milestone: 'MVP',
	runState: 'in-flight',
	depCount: 3,
	dependentCount: 2
};

describe('TicketMiniRow', () => {
	it('links the monospace id to the ticket detail route', () => {
		const { container } = render(TicketMiniRow, { props: { ticket } });

		const idLink = screen.getByRole('link', { name: 'T31' });
		expect(idLink.getAttribute('href')).toBe('/tickets/T31');
		expect(container.querySelector('a')?.className).toContain('font-mono');
	});

	it('renders the title, the status badge, and the humanized run-state badge', () => {
		render(TicketMiniRow, { props: { ticket } });

		expect(screen.getByText(ticket.title)).toBeTruthy();
		// StatusBadge renders the raw status; RunStateBadge renders a humanized label.
		expect(screen.getByText('in_progress')).toBeTruthy();
		expect(screen.getByText('In flight')).toBeTruthy();
	});
});
