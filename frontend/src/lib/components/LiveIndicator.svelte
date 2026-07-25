<script lang="ts">
	import type { LiveStatus } from '$lib/stores/live';

	// Presentational: no `$app/*` and no store imports, so it renders
	// deterministically under vitest/jsdom from supplied props. The layout owns the
	// live store and passes its current values in. A new `lastEvent` timestamp
	// briefly flashes "Updated"; `disconnected` dims the pill.
	const FLASH_MS = 1500;

	let { status, lastEvent = null }: { status: LiveStatus; lastEvent?: number | null } = $props();

	let flashing = $state(false);

	$effect(() => {
		// Re-runs whenever a fresh event arrives (tracks `lastEvent`).
		if (lastEvent === null) return;
		flashing = true;
		const timer = setTimeout(() => {
			flashing = false;
		}, FLASH_MS);
		return () => clearTimeout(timer);
	});

	const LABELS: Record<LiveStatus, string> = {
		connecting: 'Connecting…',
		live: 'Live',
		disconnected: 'Offline'
	};

	// Full literal class strings so Tailwind's JIT scanner keeps them.
	const DOT_CLASSES: Record<LiveStatus, string> = {
		connecting: 'bg-amber-400',
		live: 'bg-green-500',
		disconnected: 'bg-slate-400'
	};

	let label = $derived(flashing ? 'Updated' : LABELS[status]);
	let dotClass = $derived(flashing ? 'bg-green-500' : DOT_CLASSES[status]);
	let dimmed = $derived(status === 'disconnected' && !flashing);
</script>

<span
	class="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-slate-200 bg-surface px-2 py-0.5 text-xs font-medium {dimmed
		? 'text-muted'
		: 'text-text'}"
	aria-live="polite"
	title={label}
>
	<span class="h-2 w-2 rounded-full {dotClass}" class:animate-pulse={flashing} aria-hidden="true"
	></span>
	{label}
</span>
