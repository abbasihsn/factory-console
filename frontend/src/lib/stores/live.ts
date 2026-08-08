import { writable, type Readable } from 'svelte/store';

/**
 * Connection state of the live SSE subscription.
 * - `connecting` — an `EventSource` is opening (or reconnecting).
 * - `live` — the stream is open and receiving.
 * - `disconnected` — no stream (errored, stopped, or `EventSource` unavailable).
 */
export type LiveStatus = 'connecting' | 'live' | 'disconnected';

/**
 * A client-only live-updates handle backing the layout's SSE subscription.
 * `status` / `bump` / `lastEvent` are read-only stores; `start` / `stop` own the
 * `EventSource` lifecycle. Every incoming event is treated as an untyped
 * "something changed → refresh" trigger (`bump` increments); the body is never
 * parsed.
 */
export interface LiveStore {
	/** Current connection status. */
	readonly status: Readable<LiveStatus>;
	/** Monotonic counter incremented once per received event (starts at 0). */
	readonly bump: Readable<number>;
	/** `Date.now()` of the last received event, or `null` before the first. */
	readonly lastEvent: Readable<number | null>;
	/** Open the stream (no-op if already started or if `EventSource` is absent). */
	start(): void;
	/** Close the stream, cancel any pending reconnect, and go `disconnected`. */
	stop(): void;
	/**
	 * Re-open the stream on a fresh connection (`stop()` then `start()`), resetting
	 * the reconnect backoff so a switch never inherits a long pending delay. Used
	 * when the selected project changes: the server resolves the stream's project
	 * per CONNECTION, so only a new connection follows the new selection. No-op-safe
	 * when `EventSource` is unavailable, like `start`/`stop`.
	 */
	restart(): void;
}

/** Minimal constructor shape so tests can inject a fake `EventSource`. */
export type EventSourceCtor = new (url: string) => EventSource;

export interface LiveStoreOptions {
	/** SSE endpoint. Defaults to the backend watcher stream. */
	url?: string;
	/**
	 * `EventSource` constructor to use. Defaults to the global one, or `undefined`
	 * when it is unavailable (SSR/jsdom/old browsers) — in which case the store
	 * degrades gracefully to `disconnected` and never throws.
	 */
	eventSourceCtor?: EventSourceCtor;
	/** First reconnect delay, doubled each attempt up to `maxDelayMs`. */
	baseDelayMs?: number;
	/** Ceiling for the capped exponential backoff. */
	maxDelayMs?: number;
	/** Clock for `lastEvent` timestamps (injectable for deterministic tests). */
	now?: () => number;
}

const DEFAULT_URL = '/api/v1/events';
const DEFAULT_BASE_DELAY_MS = 1000;
const DEFAULT_MAX_DELAY_MS = 30_000;

function resolveEventSourceCtor(injected?: EventSourceCtor): EventSourceCtor | undefined {
	if (injected) return injected;
	return typeof EventSource === 'undefined' ? undefined : EventSource;
}

/**
 * Build a live-updates handle over an `EventSource`. Reconnects with capped
 * exponential backoff on error and is safe to import/start under SSR or jsdom
 * (where `EventSource` is undefined) — there it simply stays `disconnected`.
 */
export function createLiveStore(options: LiveStoreOptions = {}): LiveStore {
	const url = options.url ?? DEFAULT_URL;
	const baseDelayMs = options.baseDelayMs ?? DEFAULT_BASE_DELAY_MS;
	const maxDelayMs = options.maxDelayMs ?? DEFAULT_MAX_DELAY_MS;
	const now = options.now ?? (() => Date.now());
	const eventSourceCtor = resolveEventSourceCtor(options.eventSourceCtor);

	const status = writable<LiveStatus>('disconnected');
	const bump = writable(0);
	const lastEvent = writable<number | null>(null);

	let source: EventSource | null = null;
	let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
	let attempt = 0;
	let stopped = true;

	function clearReconnect(): void {
		if (reconnectTimer !== null) {
			clearTimeout(reconnectTimer);
			reconnectTimer = null;
		}
	}

	function closeSource(): void {
		if (source) {
			source.close();
			source = null;
		}
	}

	function scheduleReconnect(): void {
		if (stopped) return;
		clearReconnect();
		const delay = Math.min(baseDelayMs * 2 ** attempt, maxDelayMs);
		attempt += 1;
		reconnectTimer = setTimeout(connect, delay);
	}

	function connect(): void {
		if (stopped || !eventSourceCtor) return;
		status.set('connecting');
		const es = new eventSourceCtor(url);
		source = es;
		es.onopen = () => {
			attempt = 0;
			status.set('live');
		};
		// Any event is an untyped "refresh" signal — never parse the body. The
		// backend emits NAMED frames (`event: change`; see the server's
		// events_service), and per the EventSource spec a named frame does NOT
		// trigger `onmessage` — that fires only for unnamed `message` frames. So
		// listen for `change` explicitly; `onmessage` is kept for robustness against
		// an unnamed/default frame. The `ready` handshake frame is deliberately
		// ignored — `onopen` already marks the stream live, and a fresh connection
		// should not trigger a data refresh.
		const onSignal = (): void => {
			lastEvent.set(now());
			bump.update((n) => n + 1);
		};
		es.onmessage = onSignal;
		es.addEventListener('change', onSignal);
		// The server ends a stream it can no longer serve — the selection changed
		// under it — with a terminal `event: stale` frame. That is a normal
		// lifecycle event, not a failure: reconnect at once on a fresh connection
		// (which is what re-resolves the project server-side) WITHOUT the
		// `disconnected` transition and the backoff delay the error path takes.
		// Named frames never reach `onmessage`, hence the explicit listener.
		es.addEventListener('stale', () => {
			if (stopped || source !== es) return;
			clearReconnect();
			closeSource();
			attempt = 0;
			connect();
		});
		es.onerror = () => {
			// Drop the native auto-reconnect and drive our own capped backoff.
			closeSource();
			status.set('disconnected');
			scheduleReconnect();
		};
	}

	function start(): void {
		if (!stopped) return;
		stopped = false;
		if (!eventSourceCtor) {
			// Graceful degradation: no EventSource — manual Reload still works.
			status.set('disconnected');
			return;
		}
		attempt = 0;
		connect();
	}

	function stop(): void {
		stopped = true;
		clearReconnect();
		closeSource();
		status.set('disconnected');
	}

	function restart(): void {
		stop();
		// Reset before start() so the no-EventSource path stays consistent too: a
		// switch must never inherit the pending delay a previous failure walked up.
		attempt = 0;
		start();
	}

	return { status, bump, lastEvent, start, stop, restart };
}
