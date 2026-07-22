<script lang="ts">
	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with a supplied ticket. One summary row for the index
	// list — id link, title, the two badges, optional track/milestone chips, and
	// a dependencies → dependents count.
	import type { TicketSummary } from '$lib/api';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import RunStateBadge from '$lib/components/RunStateBadge.svelte';

	let { ticket }: { ticket: TicketSummary } = $props();
</script>

<div class="flex items-center gap-3 px-4 py-3">
	<a href="/tickets/{ticket.id}" class="shrink-0 font-mono text-sm text-accent hover:underline">
		{ticket.id}
	</a>
	<span class="min-w-0 flex-1 truncate text-text" title={ticket.title}>{ticket.title}</span>
	<StatusBadge status={ticket.status} />
	<RunStateBadge runState={ticket.runState} />
	{#if ticket.track}
		<span
			class="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600"
			title="track"
		>
			{ticket.track}
		</span>
	{/if}
	{#if ticket.milestone}
		<span
			class="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600"
			title="milestone"
		>
			{ticket.milestone}
		</span>
	{/if}
	<span class="shrink-0 font-mono text-xs text-muted" title="dependencies → dependents">
		{ticket.depCount} → {ticket.dependentCount}
	</span>
</div>
