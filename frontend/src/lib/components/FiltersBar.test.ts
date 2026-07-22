import { fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import FiltersBar from '$lib/components/FiltersBar.svelte';

// FiltersBar is presentational and `$app`-free: navigation is injected via
// `onNavigate`, so it unit-tests without a router. Selects navigate immediately;
// the search box is debounced. `onNavigate` receives the query string WITHOUT a
// leading `?`, and empty controls are omitted from it.
function baseProps() {
	return {
		filters: { status: '', track: '', milestone: '', q: '' },
		statuses: ['todo', 'in_progress', 'done'],
		tracks: ['frontend', 'backend'],
		milestones: ['MVP'],
		onNavigate: vi.fn()
	};
}

describe('FiltersBar', () => {
	afterEach(() => {
		vi.useRealTimers();
	});

	it('navigates immediately when a select changes, omitting empty controls', async () => {
		const props = baseProps();
		render(FiltersBar, { props });

		await fireEvent.change(screen.getByLabelText('Filter by status'), {
			target: { value: 'done' }
		});

		expect(props.onNavigate).toHaveBeenCalledTimes(1);
		expect(props.onNavigate).toHaveBeenCalledWith('status=done');
	});

	it('debounces the search input by 250ms, then navigates once', async () => {
		vi.useFakeTimers();
		const props = baseProps();
		render(FiltersBar, { props });

		await fireEvent.input(screen.getByLabelText('Search tickets'), {
			target: { value: 'route' }
		});

		// Nothing fires until the debounce window elapses.
		expect(props.onNavigate).not.toHaveBeenCalled();

		vi.advanceTimersByTime(250);

		expect(props.onNavigate).toHaveBeenCalledTimes(1);
		expect(props.onNavigate).toHaveBeenCalledWith('q=route');
	});

	it('carries the already-active filters into the query on a select change', async () => {
		const props = baseProps();
		props.filters = { status: 'done', track: '', milestone: '', q: 'auth' };
		render(FiltersBar, { props });

		await fireEvent.change(screen.getByLabelText('Filter by track'), {
			target: { value: 'frontend' }
		});

		// The changed track plus the active status + search term; empty milestone dropped.
		expect(props.onNavigate).toHaveBeenCalledWith('status=done&track=frontend&q=auth');
	});
});
