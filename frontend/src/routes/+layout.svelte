<script lang="ts">
	import '../app.css';
	import { invalidateAll } from '$app/navigation';
	import TopBar from '$lib/components/TopBar.svelte';
	import LiveIndicator from '$lib/components/LiveIndicator.svelte';
	import { createLiveStore } from '$lib/stores/live';
	import { onMount, type Snippet } from 'svelte';
	import type { LayoutData } from './$types';

	let { data, children }: { data: LayoutData; children?: Snippet } = $props();

	// Client-only SSE subscription: any event refreshes the current route the same
	// way the Reload button does. Guarded/no-op without EventSource, so the shell
	// degrades to manual Reload.
	const live = createLiveStore();
	const { status, bump, lastEvent } = live;

	onMount(() => {
		live.start();
		return live.stop;
	});

	$effect(() => {
		// Re-runs on each new event; the initial bump of 0 is skipped.
		if ($bump > 0) invalidateAll();
	});
</script>

<div class="min-h-screen bg-bg text-text">
	<TopBar project={data.project} onReload={invalidateAll} />
	<div class="mx-auto flex max-w-5xl justify-end px-4 pt-3">
		<LiveIndicator status={$status} lastEvent={$lastEvent} />
	</div>
	<main class="mx-auto max-w-5xl px-4 py-6">
		{@render children?.()}
	</main>
</div>
