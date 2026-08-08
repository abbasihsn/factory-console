<script lang="ts">
	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with a supplied milestone. Renders a titled section listing
	// each item's live run-state, text, and — when present — a monospace id link to
	// the ticket.
	//
	// THE READ-ONLY CHECKBOX GLYPH IS GONE, replaced by the same `RunStateBadge` the
	// ticket list and detail views use. The glyph reflected a `[x]` somebody typed into
	// ROADMAP.md — derived state in a committed file, which App Factory v3 §4 forbids
	// and `factory-doctor` FAILs a repository for carrying. It went stale the moment a
	// lane merged, and it could contradict the badge on the very ticket it linked to.
	// Sharing the badge component is what makes that contradiction unrepresentable:
	// there is one renderer for one value from one source.
	//
	// A RoadmapItem still carries no TicketSummary, so this deliberately does NOT use
	// TicketMiniRow — with the available data the row is badge + text + id link.
	import type { RoadmapMilestone } from '$lib/api';
	import RunStateBadge from '$lib/components/RunStateBadge.svelte';

	let { milestone }: { milestone: RoadmapMilestone } = $props();
</script>

<section class="rounded-lg border border-slate-200 bg-surface px-4 py-3">
	<h2 class="text-lg font-semibold text-text">{milestone.name}</h2>
	<ul class="mt-2 space-y-1">
		{#each milestone.items ?? [] as item}
			<li class="flex items-center gap-2 text-text">
				<!-- A null runState means the item names no ticket, which is NOT the same as
				     a ticket whose state is unknown. Rendering nothing is the honest answer:
				     an `unknown` pill here would assert the factory has never heard of a
				     ticket that does not exist. The empty span holds the column so labels
				     still line up down the list. -->
				<span class="shrink-0">
					{#if item.runState}
						<RunStateBadge runState={item.runState} />
					{/if}
				</span>
				<span class="min-w-0 flex-1">{item.text}</span>
				{#if item.ticketId}
					<a href="/tickets/{item.ticketId}" class="font-mono text-sm text-accent hover:underline">
						{item.ticketId}
					</a>
				{/if}
			</li>
		{/each}
	</ul>
</section>
