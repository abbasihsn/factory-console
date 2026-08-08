import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createCoalescer } from '$lib/stores/coalesce';

describe('createCoalescer', () => {
	beforeEach(() => vi.useFakeTimers());
	afterEach(() => vi.useRealTimers());

	it('runs once after the delay for a single schedule', () => {
		const run = vi.fn();
		createCoalescer(run, 300).schedule();

		expect(run).not.toHaveBeenCalled();
		vi.advanceTimersByTime(300);
		expect(run).toHaveBeenCalledTimes(1);
	});

	it('folds a burst of schedules into ONE run', () => {
		// The bug: the server debounces per PATH, so a lane rewriting 10 files emitted
		// ~10 events and the layout answered each with a full invalidateAll().
		const run = vi.fn();
		const coalescer = createCoalescer(run, 300);

		for (let i = 0; i < 10; i += 1) {
			coalescer.schedule();
			vi.advanceTimersByTime(50); // events arriving faster than the delay
		}
		vi.advanceTimersByTime(300);

		expect(run).toHaveBeenCalledTimes(1);
	});

	it('runs on the TRAILING edge, so it reads settled state', () => {
		const run = vi.fn();
		const coalescer = createCoalescer(run, 300);

		coalescer.schedule();
		vi.advanceTimersByTime(299);
		coalescer.schedule(); // one more write lands just before the timer fires
		vi.advanceTimersByTime(299);
		expect(run).not.toHaveBeenCalled(); // still deferring — writes are ongoing

		vi.advanceTimersByTime(1);
		expect(run).toHaveBeenCalledTimes(1);
	});

	it('runs again for a burst that arrives after the previous one completed', () => {
		const run = vi.fn();
		const coalescer = createCoalescer(run, 300);

		coalescer.schedule();
		vi.advanceTimersByTime(300);
		coalescer.schedule();
		vi.advanceTimersByTime(300);

		expect(run).toHaveBeenCalledTimes(2);
	});

	it('cancel drops a pending run', () => {
		const run = vi.fn();
		const coalescer = createCoalescer(run, 300);

		coalescer.schedule();
		coalescer.cancel();
		vi.advanceTimersByTime(1000);

		expect(run).not.toHaveBeenCalled();
	});
});
