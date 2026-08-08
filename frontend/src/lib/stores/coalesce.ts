/**
 * Fold a burst of calls into one trailing call.
 *
 * Written for the SSE refresh path. The server debounces file events only PER
 * PATH (`watcher_real.py`, 0.15s), so a factory lane rewriting N files emits ~N
 * events over a second or two. The layout answered each one with a full
 * `invalidateAll()`, and each of those re-runs the layout load AND the current
 * page load — 3 HTTP round-trips on `/runs`, a whole Cytoscape re-layout on
 * `/graph` — so an active factory run turned the open page into a re-fetch loop.
 *
 * TRAILING edge, deliberately: each call restarts the timer, so the burst costs
 * exactly one run and that run happens once the writes have SETTLED. A leading
 * edge would fire on the first event and read a half-written state.
 */
export interface Coalescer {
	/** Request a run. Restarts the delay; the wrapped function runs once it elapses. */
	schedule(): void;
	/** Drop any pending run. Safe to call repeatedly; call it on teardown. */
	cancel(): void;
}

export function createCoalescer(run: () => void, delayMs: number): Coalescer {
	let timer: ReturnType<typeof setTimeout> | undefined;

	return {
		schedule() {
			clearTimeout(timer);
			timer = setTimeout(() => {
				timer = undefined;
				run();
			}, delayMs);
		},
		cancel() {
			clearTimeout(timer);
			timer = undefined;
		}
	};
}
