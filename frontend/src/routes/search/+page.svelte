<script lang="ts">
	import type { PageData } from './$types';
	import TicketMiniRow from '$lib/components/TicketMiniRow.svelte';

	let { data }: { data: PageData } = $props();

	const q = $derived(data.q.trim());
	const results = $derived(data.results);
</script>

<div class="space-y-4">
	<h1 class="text-2xl font-semibold text-text">Search</h1>

	{#if q === ''}
		<p class="rounded-lg border border-slate-200 bg-surface px-4 py-8 text-center text-muted">
			Type a term in the search box to find tickets.
		</p>
	{:else}
		<p class="text-sm text-muted">
			Results for <span class="font-mono text-text">{q}</span>
		</p>

		{#if results.length === 0}
			<p class="rounded-lg border border-slate-200 bg-surface px-4 py-8 text-center text-muted">
				No tickets match &ldquo;{q}&rdquo;.
			</p>
		{:else}
			<ul
				class="divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-surface"
			>
				{#each results as hit (hit.ticket.id)}
					<li>
						<TicketMiniRow ticket={hit.ticket} />
						{#if hit.matchedFields && hit.matchedFields.length > 0}
							<p class="px-4 pb-2 text-xs text-muted">matched: {hit.matchedFields.join(', ')}</p>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
</div>
