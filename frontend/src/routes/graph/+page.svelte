<script lang="ts">
	import type { PageData } from './$types';
	import DepGraph from '$lib/components/DepGraph.svelte';

	let { data }: { data: PageData } = $props();

	// `nodes` is optional in the schema; guard with `?? []` so the empty-state
	// message shows when the backend returns no nodes (or omits the field).
	const nodes = $derived(data.graph.nodes ?? []);
</script>

<div class="space-y-4">
	<h1 class="text-2xl font-semibold text-text">Dependency graph</h1>

	{#if nodes.length === 0}
		<p class="rounded-lg border border-slate-200 bg-surface px-4 py-8 text-center text-muted">
			No tickets to graph yet.
		</p>
	{:else}
		<div class="rounded-lg border border-slate-200 bg-surface">
			<DepGraph graph={data.graph} />
		</div>
	{/if}
</div>
