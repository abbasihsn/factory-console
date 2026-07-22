<script lang="ts">
	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with a supplied ticket. A compact dep-list row — id link,
	// title, and the two badges — for the neighborhood route's resolved-deps lists.
	// Unlike the index's TicketRow it carries no track/milestone chips or dep counts.
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
</div>
