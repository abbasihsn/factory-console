import { render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import LiveIndicator from './LiveIndicator.svelte';

afterEach(() => {
	vi.useRealTimers();
});

describe('LiveIndicator', () => {
	it('labels each connection status when no event has arrived', () => {
		const { unmount } = render(LiveIndicator, { props: { status: 'live' } });
		expect(screen.getByText('Live')).toBeTruthy();
		unmount();

		render(LiveIndicator, { props: { status: 'connecting' } });
		expect(screen.getByText('Connecting…')).toBeTruthy();
	});

	it('dims the pill (text-muted) when disconnected', () => {
		render(LiveIndicator, { props: { status: 'disconnected' } });
		const pill = screen.getByText('Offline');
		expect(pill.className).toContain('text-muted');
	});

	it('flashes "Updated" after an event arrives', async () => {
		render(LiveIndicator, { props: { status: 'live', lastEvent: Date.now() } });
		await waitFor(() => expect(screen.getByText('Updated')).toBeTruthy());
		// The flash overrides the plain status label while active.
		expect(screen.queryByText('Live')).toBeNull();
	});

	it('clears the flash back to the status label after the flash window', async () => {
		vi.useFakeTimers();
		const { rerender } = render(LiveIndicator, { props: { status: 'live', lastEvent: 1 } });
		await vi.advanceTimersByTimeAsync(0);
		expect(screen.getByText('Updated')).toBeTruthy();

		await vi.advanceTimersByTimeAsync(1500);
		expect(screen.queryByText('Updated')).toBeNull();
		expect(screen.getByText('Live')).toBeTruthy();

		// A fresh event timestamp re-triggers the flash.
		await rerender({ status: 'live', lastEvent: 2 });
		await vi.advanceTimersByTimeAsync(0);
		expect(screen.getByText('Updated')).toBeTruthy();
	});
});
