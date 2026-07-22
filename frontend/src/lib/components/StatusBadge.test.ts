import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import StatusBadge from '$lib/components/StatusBadge.svelte';

// `status` is a free string on Ticket/TicketSummary: the four known workflow
// statuses render as color pills, anything else as a neutral pill showing the
// raw string. One container snapshot pins each variant's markup.
describe('StatusBadge', () => {
	for (const status of ['todo', 'in-progress', 'done', 'blocked']) {
		it(`renders a color pill for the known status "${status}"`, () => {
			const { container } = render(StatusBadge, { props: { status } });
			expect(container.innerHTML).toMatchSnapshot();
		});
	}

	it('renders a neutral pill for an unknown status, showing the raw string', () => {
		const { container } = render(StatusBadge, { props: { status: 'archived' } });
		expect(container.innerHTML).toMatchSnapshot();
		expect(screen.getByText('archived')).toBeTruthy();
	});
});
