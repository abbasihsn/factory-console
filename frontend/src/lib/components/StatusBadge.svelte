<script lang="ts">
	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with supplied props. `status` is a free string (enum
	// passthrough on Ticket/TicketSummary), so it stays a string, not a union.
	let { status }: { status: string } = $props();

	// Known workflow statuses map to a color pill; any other string falls back to
	// the neutral pill and is rendered verbatim.
	const COLORS: Record<string, string> = {
		todo: 'bg-gray-100 text-gray-700',
		'in-progress': 'bg-amber-100 text-amber-800',
		done: 'bg-green-100 text-green-800',
		blocked: 'bg-red-100 text-red-800'
	};
	const NEUTRAL = 'bg-gray-100 text-muted';
	const PILL = 'inline-flex items-center rounded px-2 py-0.5 text-xs font-medium';

	let color = $derived(COLORS[status] ?? NEUTRAL);
</script>

<span class="{PILL} {color}">{status}</span>
