import { fireEvent, render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// NavSearch is the one header piece that owns navigation, so stub `$app/navigation`
// and assert on the resolved `goto` target rather than driving a real router.
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

import { goto } from '$app/navigation';
import NavSearch from '$lib/components/NavSearch.svelte';

const gotoMock = vi.mocked(goto);

describe('NavSearch', () => {
	beforeEach(() => {
		gotoMock.mockReset();
	});

	it('renders the nav links with their hrefs', () => {
		render(NavSearch);

		expect(screen.getByRole('link', { name: 'Home' }).getAttribute('href')).toBe('/');
		expect(screen.getByRole('link', { name: 'Graph' }).getAttribute('href')).toBe('/graph');
		expect(screen.getByRole('link', { name: 'Roadmap' }).getAttribute('href')).toBe('/roadmap');
		expect(screen.getByRole('link', { name: 'Spend' }).getAttribute('href')).toBe('/spend');
		expect(screen.getByRole('link', { name: 'Runs' }).getAttribute('href')).toBe('/runs');
	});

	it('navigates to /search with the encoded term on submit', async () => {
		render(NavSearch);

		const box = screen.getByLabelText('Search tickets');
		await fireEvent.input(box, { target: { value: 'auth route' } });
		await fireEvent.submit(box.closest('form')!);

		expect(gotoMock).toHaveBeenCalledTimes(1);
		expect(gotoMock).toHaveBeenCalledWith('/search?q=auth%20route');
	});

	it('trims the term before navigating', async () => {
		render(NavSearch);

		const box = screen.getByLabelText('Search tickets');
		await fireEvent.input(box, { target: { value: '  graph  ' } });
		await fireEvent.submit(box.closest('form')!);

		expect(gotoMock).toHaveBeenCalledWith('/search?q=graph');
	});

	it('navigates to /search with an empty q when the box is blank', async () => {
		render(NavSearch);

		const box = screen.getByLabelText('Search tickets');
		await fireEvent.submit(box.closest('form')!);

		expect(gotoMock).toHaveBeenCalledTimes(1);
		expect(gotoMock).toHaveBeenCalledWith('/search?q=');
	});
});
