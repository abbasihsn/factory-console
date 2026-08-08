<script lang="ts">
	import '../app.css';
	import { invalidateAll } from '$app/navigation';
	import TopBar from '$lib/components/TopBar.svelte';
	import LiveIndicator from '$lib/components/LiveIndicator.svelte';
	import ProjectStatusBanner from '$lib/components/ProjectStatusBanner.svelte';
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

	// The stream follows the selection. Contract this relies on (T115): the server
	// resolves the stream's project ONCE PER CONNECTION, so a switch only takes
	// effect on a NEW `/api/v1/events` connection — hence the restart, which also
	// clears the backoff so rapid switching cannot walk the delay up.
	//
	// Keyed on the selected id VALUE, not on `data` itself: `invalidateAll()` above
	// re-runs the layout load on every SSE bump and hands back a fresh object, so
	// keying on identity would restart the stream on every file change. Reading the
	// id off the layout data (rather than the switcher's own callback) also catches
	// a selection changed in ANOTHER tab, which this tab learns about on a re-load.
	let seenSelectedId: string | null = null;
	let selectionTracked = false;
	$effect(() => {
		const selectedId = data.selectedId;
		// The first run only records the id — the stream started with it already.
		if (selectionTracked && selectedId !== seenSelectedId) live.restart();
		selectionTracked = true;
		seenSelectedId = selectedId;
	});

	// The registry row the shell is serving, for the condition banner below the
	// top bar. `null` covers single-project mode (no registry rows at all), a
	// selection the registry cannot name, and the reserved unregistered `session`
	// row: a `factory-console PATH` boot with no `.factory/` dir is the ORDINARY
	// state of a fresh clone, not a fault, and the banner must not fire for it —
	// only for a row someone actually registered.
	const selectedProject = $derived(
		data.projects.find((project) => project.id === data.selectedId && project.registered) ?? null
	);
</script>

<div class="min-h-screen bg-bg text-text">
	<TopBar
		project={data.project}
		projects={data.projects}
		selectedId={data.selectedId}
		onReload={invalidateAll}
	/>
	<ProjectStatusBanner project={selectedProject} />
	<div class="mx-auto flex max-w-5xl justify-end px-4 pt-3">
		<LiveIndicator status={$status} lastEvent={$lastEvent} />
	</div>
	<main class="mx-auto max-w-5xl px-4 py-6">
		{@render children?.()}
	</main>
</div>
