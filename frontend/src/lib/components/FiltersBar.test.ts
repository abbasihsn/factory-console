import { fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import FiltersBar from '$lib/components/FiltersBar.svelte';

// FiltersBar is presentational and `$app`-free: navigation is injected via
// `onNavigate`, so it unit-tests without a router. Selects navigate immediately;
// the search box is debounced. `onNavigate` receives the resolved four-field
// `Filters` object (the route owns turning it into a URL); empty controls stay as
// empty strings rather than being dropped.
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
		expect(props.onNavigate).toHaveBeenCalledWith({
			status: 'done',
			track: '',
			milestone: '',
			q: ''
		});
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
		expect(props.onNavigate).toHaveBeenCalledWith({
			status: '',
			track: '',
			milestone: '',
			q: 'route'
		});
	});

	it('carries the already-active filters into the query on a select change', async () => {
		const props = baseProps();
		props.filters = { status: 'done', track: '', milestone: '', q: 'auth' };
		render(FiltersBar, { props });

		await fireEvent.change(screen.getByLabelText('Filter by track'), {
			target: { value: 'frontend' }
		});

		// The changed track plus the active status + search term; empty milestone stays ''.
		expect(props.onNavigate).toHaveBeenCalledWith({
			status: 'done',
			track: 'frontend',
			milestone: '',
			q: 'auth'
		});
	});

	it('cancels a pending search debounce when a select changes, navigating once', async () => {
		vi.useFakeTimers();
		const props = baseProps();
		render(FiltersBar, { props });

		// Start a debounced search, then change a select before the timer elapses.
		await fireEvent.input(screen.getByLabelText('Search tickets'), {
			target: { value: 'route' }
		});
		await fireEvent.change(screen.getByLabelText('Filter by status'), {
			target: { value: 'done' }
		});

		// The select navigated immediately, carrying the typed term.
		expect(props.onNavigate).toHaveBeenCalledTimes(1);
		expect(props.onNavigate).toHaveBeenCalledWith({
			status: 'done',
			track: '',
			milestone: '',
			q: 'route'
		});

		// The superseded debounce must not fire a second navigation.
		vi.advanceTimersByTime(250);
		expect(props.onNavigate).toHaveBeenCalledTimes(1);
	});

	it('re-syncs the box and cancels a stale debounce on an external reset', async () => {
		vi.useFakeTimers();
		const props = baseProps();
		// The search was already empty, so a reset to `/` leaves filters.q unchanged
		// — the case where the one-way `value=` seed alone would not clear the box.
		const { rerender } = render(FiltersBar, { props });

		const box = screen.getByLabelText('Search tickets') as HTMLInputElement;
		// Type a term (arming the 250ms debounce), then leave the box — as clicking
		// the empty state's external "clear filters" link would (the box loses focus).
		await fireEvent.input(box, { target: { value: 'zzz' } });
		box.blur();

		// A navigation lands: a fresh filters object with q still ''.
		await rerender({ ...props, filters: { status: '', track: '', milestone: '', q: '' } });

		// The box is re-synced to the (empty) URL term...
		expect(box.value).toBe('');
		// ...and the abandoned debounce was cancelled, so nothing re-applies `zzz`.
		vi.advanceTimersByTime(250);
		expect(props.onNavigate).not.toHaveBeenCalled();
	});

	it('leaves an actively-typed term alone when a navigation lands mid-edit', async () => {
		vi.useFakeTimers();
		const props = baseProps();
		const { rerender } = render(FiltersBar, { props });

		const box = screen.getByLabelText('Search tickets') as HTMLInputElement;
		// The user is focused and typing (debounce armed) when a navigation resolves.
		box.focus();
		await fireEvent.input(box, { target: { value: 'route' } });

		await rerender({ ...props, filters: { status: '', track: '', milestone: '', q: 'route' } });

		// The focused box keeps its term and its own debounce, which still fires once.
		expect(box.value).toBe('route');
		vi.advanceTimersByTime(250);
		expect(props.onNavigate).toHaveBeenCalledTimes(1);
		expect(props.onNavigate).toHaveBeenCalledWith({
			status: '',
			track: '',
			milestone: '',
			q: 'route'
		});
	});
});
