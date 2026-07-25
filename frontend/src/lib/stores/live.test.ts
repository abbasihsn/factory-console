import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createLiveStore, type EventSourceCtor } from './live';

// A minimal fake EventSource: records the URL, exposes the handlers the store
// wires up, and lets a test drive open/message/error deterministically. Cast to
// the DOM `EventSource` shape at the injection point (we only use these members).
class FakeEventSource {
	static instances: FakeEventSource[] = [];

	url: string;
	closed = false;
	onopen: (() => void) | null = null;
	onmessage: (() => void) | null = null;
	onerror: (() => void) | null = null;
	// Named-event listeners registered via addEventListener — the real backend
	// sends `event: change` frames, which reach these (not onmessage).
	listeners = new Map<string, Array<() => void>>();

	constructor(url: string) {
		this.url = url;
		FakeEventSource.instances.push(this);
	}

	close(): void {
		this.closed = true;
	}

	addEventListener(type: string, cb: () => void): void {
		const forType = this.listeners.get(type) ?? [];
		forType.push(cb);
		this.listeners.set(type, forType);
	}

	emitOpen(): void {
		this.onopen?.();
	}
	emitMessage(): void {
		this.onmessage?.();
	}
	emitEvent(type: string): void {
		for (const cb of this.listeners.get(type) ?? []) cb();
	}
	emitError(): void {
		this.onerror?.();
	}

	static get last(): FakeEventSource {
		const es = FakeEventSource.instances.at(-1);
		if (!es) throw new Error('no EventSource opened');
		return es;
	}

	static reset(): void {
		FakeEventSource.instances = [];
	}
}

const fakeCtor = FakeEventSource as unknown as EventSourceCtor;

beforeEach(() => {
	FakeEventSource.reset();
	vi.useFakeTimers();
});

afterEach(() => {
	vi.useRealTimers();
});

describe('createLiveStore', () => {
	it('starts disconnected before start()', () => {
		const live = createLiveStore({ eventSourceCtor: fakeCtor });
		expect(get(live.status)).toBe('disconnected');
		expect(get(live.bump)).toBe(0);
		expect(get(live.lastEvent)).toBeNull();
	});

	it('opens the events endpoint and goes connecting → live on open', () => {
		const live = createLiveStore({ eventSourceCtor: fakeCtor });
		live.start();

		expect(get(live.status)).toBe('connecting');
		expect(FakeEventSource.last.url).toBe('/api/v1/events');

		FakeEventSource.last.emitOpen();
		expect(get(live.status)).toBe('live');
	});

	it('honors a custom url', () => {
		const live = createLiveStore({ eventSourceCtor: fakeCtor, url: '/custom/events' });
		live.start();
		expect(FakeEventSource.last.url).toBe('/custom/events');
	});

	it('bumps and stamps lastEvent on each message without parsing the body', () => {
		const live = createLiveStore({ eventSourceCtor: fakeCtor, now: () => 4242 });
		live.start();
		FakeEventSource.last.emitOpen();

		FakeEventSource.last.emitMessage();
		expect(get(live.bump)).toBe(1);
		expect(get(live.lastEvent)).toBe(4242);

		FakeEventSource.last.emitMessage();
		expect(get(live.bump)).toBe(2);
	});

	it('bumps and stamps lastEvent on a named change event (the frame the backend sends)', () => {
		const live = createLiveStore({ eventSourceCtor: fakeCtor, now: () => 4242 });
		live.start();
		FakeEventSource.last.emitOpen();

		// The server emits `event: change` — a named frame — which the EventSource
		// spec delivers to addEventListener('change'), NOT onmessage.
		FakeEventSource.last.emitEvent('change');
		expect(get(live.bump)).toBe(1);
		expect(get(live.lastEvent)).toBe(4242);

		FakeEventSource.last.emitEvent('change');
		expect(get(live.bump)).toBe(2);
	});

	it('goes disconnected on error and reconnects after the base delay', () => {
		const live = createLiveStore({ eventSourceCtor: fakeCtor, baseDelayMs: 1000 });
		live.start();
		FakeEventSource.last.emitOpen();

		FakeEventSource.last.emitError();
		expect(get(live.status)).toBe('disconnected');
		expect(FakeEventSource.instances).toHaveLength(1);

		vi.advanceTimersByTime(1000);
		expect(FakeEventSource.instances).toHaveLength(2);
		expect(get(live.status)).toBe('connecting');

		FakeEventSource.last.emitOpen();
		expect(get(live.status)).toBe('live');
	});

	it('backs off exponentially up to the cap across repeated errors', () => {
		const live = createLiveStore({
			eventSourceCtor: fakeCtor,
			baseDelayMs: 1000,
			maxDelayMs: 4000
		});
		live.start();

		// attempt 0 → 1000ms
		FakeEventSource.last.emitError();
		vi.advanceTimersByTime(999);
		expect(FakeEventSource.instances).toHaveLength(1);
		vi.advanceTimersByTime(1);
		expect(FakeEventSource.instances).toHaveLength(2);

		// attempt 1 → 2000ms
		FakeEventSource.last.emitError();
		vi.advanceTimersByTime(2000);
		expect(FakeEventSource.instances).toHaveLength(3);

		// attempt 2 → 4000ms (capped)
		FakeEventSource.last.emitError();
		vi.advanceTimersByTime(4000);
		expect(FakeEventSource.instances).toHaveLength(4);

		// attempt 3 → still capped at 4000ms, not 8000ms
		FakeEventSource.last.emitError();
		vi.advanceTimersByTime(4000);
		expect(FakeEventSource.instances).toHaveLength(5);
	});

	it('resets the backoff after a successful open', () => {
		const live = createLiveStore({
			eventSourceCtor: fakeCtor,
			baseDelayMs: 1000,
			maxDelayMs: 8000
		});
		live.start();

		FakeEventSource.last.emitError();
		vi.advanceTimersByTime(1000); // reconnect #2
		FakeEventSource.last.emitError();
		vi.advanceTimersByTime(2000); // reconnect #3
		FakeEventSource.last.emitOpen(); // success resets attempt

		FakeEventSource.last.emitError();
		vi.advanceTimersByTime(999);
		expect(FakeEventSource.instances).toHaveLength(3);
		vi.advanceTimersByTime(1); // back to the 1000ms base
		expect(FakeEventSource.instances).toHaveLength(4);
	});

	it('stop() closes the stream, cancels a pending reconnect, and goes disconnected', () => {
		const live = createLiveStore({ eventSourceCtor: fakeCtor, baseDelayMs: 1000 });
		live.start();
		const first = FakeEventSource.last;
		FakeEventSource.last.emitError();

		live.stop();
		expect(get(live.status)).toBe('disconnected');
		expect(first.closed).toBe(true);

		// The scheduled reconnect must not fire after stop().
		vi.advanceTimersByTime(10_000);
		expect(FakeEventSource.instances).toHaveLength(1);
	});

	it('start() is idempotent — a second call opens no extra stream', () => {
		const live = createLiveStore({ eventSourceCtor: fakeCtor });
		live.start();
		live.start();
		expect(FakeEventSource.instances).toHaveLength(1);
	});

	it('degrades gracefully when no EventSource is available', () => {
		// No injected ctor and jsdom has no global EventSource.
		const live = createLiveStore();
		expect(() => live.start()).not.toThrow();
		expect(get(live.status)).toBe('disconnected');
		expect(() => live.stop()).not.toThrow();
	});
});
