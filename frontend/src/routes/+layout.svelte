<script lang="ts">
	import '../app.css';
	import { invalidateAll } from '$app/navigation';
	import TopBar from '$lib/components/TopBar.svelte';
	import type { Snippet } from 'svelte';
	import type { LayoutData } from './$types';

	let { data, children }: { data: LayoutData; children?: Snippet } = $props();
</script>

<div class="min-h-screen bg-bg text-text">
	<TopBar project={data.project} onReload={invalidateAll} />
	<main class="mx-auto max-w-5xl px-4 py-6">
		{@render children?.()}
	</main>
</div>
