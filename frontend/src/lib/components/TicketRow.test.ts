import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import TicketRow from '$lib/components/TicketRow.svelte';
import type { TicketSummary } from '$lib/api';

// TicketRow is presentational: render it with a supplied ticket and assert the id
// link target, title, both badges, chips, and the dep → dependent counts. The
// inline snapshot targets the id `<a>` element (a single text child), not the
// container/row, so it carries no whitespace-only sibling text node — which the
// repo's trailing-whitespace hook would strip and desync (see StatusBadge.test.ts).
const ticket: TicketSummary = {
	id: 'T30',
	title: 'Ticket list route with server-side filter + search',
	status: 'in_progress',
	track: 'frontend',
	milestone: 'MVP',
	runState: 'in-flight',
	depCount: 3,
	dependentCount: 2
};

describe('TicketRow', () => {
	it('links the monospace id to the ticket detail route', () => {
		const { container } = render(TicketRow, { props: { ticket } });

		const idLink = screen.getByRole('link', { name: 'T30' });
		expect(idLink.getAttribute('href')).toBe('/tickets/T30');
		expect(container.querySelector('a')?.className).toContain('font-mono');

		expect(container.querySelector('a')).toMatchInlineSnapshot(`
			<a
			  class="shrink-0 font-mono text-sm text-accent hover:underline"
			  href="/tickets/T30"
			>
			  T30
			</a>
		`);
	});

	it('renders the title, both badges, chips, and the dep counts', () => {
		render(TicketRow, { props: { ticket } });

		expect(screen.getByText(ticket.title)).toBeTruthy();
		// StatusBadge renders the raw status; RunStateBadge renders a humanized label.
		expect(screen.getByText('in_progress')).toBeTruthy();
		expect(screen.getByText('In flight')).toBeTruthy();
		// Track + milestone chips.
		expect(screen.getByText('frontend')).toBeTruthy();
		expect(screen.getByText('MVP')).toBeTruthy();
		// depCount → dependentCount indicator.
		expect(screen.getByText('3 → 2')).toBeTruthy();
	});

	it('omits the track and milestone chips when those values are null', () => {
		const bare: TicketSummary = { ...ticket, id: 'T99', track: null, milestone: null };
		render(TicketRow, { props: { ticket: bare } });

		expect(screen.queryByText('frontend')).toBeNull();
		expect(screen.queryByText('MVP')).toBeNull();
	});
});
