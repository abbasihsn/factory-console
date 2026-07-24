<script lang="ts">
	import type { PageData } from './$types';
	import MilestoneSection from '$lib/components/MilestoneSection.svelte';
	import MarkdownBody from '$lib/components/MarkdownBody.svelte';

	let { data }: { data: PageData } = $props();
</script>

<div class="space-y-4">
	<h1 class="text-2xl font-semibold text-text">Roadmap</h1>

	{#if !data.present}
		<p class="rounded-lg border border-slate-200 bg-surface px-4 py-8 text-center text-muted">
			This project has no roadmap.
		</p>
	{:else}
		{#each data.roadmap.milestones ?? [] as milestone}
			<MilestoneSection {milestone} />
		{/each}
		<MarkdownBody html={data.roadmap.bodyHtml} />
	{/if}
</div>
