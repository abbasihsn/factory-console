<script lang="ts">
	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with a supplied milestone. Renders a titled section listing
	// each item's read-only checkbox state, text, and — when present — a monospace id
	// link to the ticket. A RoadmapItem carries only text/ticketId/done (no full
	// TicketSummary or status), so this deliberately does NOT use TicketMiniRow or
	// StatusBadge: with the available data the row reduces to glyph + text + id link.
	import type { RoadmapMilestone } from '$lib/api';

	let { milestone }: { milestone: RoadmapMilestone } = $props();

	// A read-only checkbox glyph reflecting `done`: checked/unchecked box for
	// true/false, a neutral dot when the item carries no checkbox (null/undefined).
	function glyph(done: boolean | null | undefined): string {
		if (done === true) return '☑';
		if (done === false) return '☐';
		return '·';
	}

	function glyphLabel(done: boolean | null | undefined): string {
		if (done === true) return 'done';
		if (done === false) return 'not done';
		return 'no checkbox state';
	}
</script>

<section class="rounded-lg border border-slate-200 bg-surface px-4 py-3">
	<h2 class="text-lg font-semibold text-text">{milestone.name}</h2>
	<ul class="mt-2 space-y-1">
		{#each milestone.items ?? [] as item}
			<li class="flex items-center gap-2 text-text">
				<span
					class="shrink-0 text-muted"
					aria-label={glyphLabel(item.done)}
					title={glyphLabel(item.done)}
				>
					{glyph(item.done)}
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
