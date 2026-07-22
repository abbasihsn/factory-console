<script lang="ts">
	import { goto } from '$app/navigation';
	import type { PageData } from './$types';
	import FiltersBar from '$lib/components/FiltersBar.svelte';
	import TicketRow from '$lib/components/TicketRow.svelte';

	let { data }: { data: PageData } = $props();

	// The filter dropdowns offer the distinct values present in the loaded tickets
	// (sorted), unioned with the currently-active filter value so the active
	// selection stays selectable even when nothing on this page carries it.
	// Project-wide option lists are deferred to v1 (the ticket's "dynamised in
	// v1") — the MVP deliberately avoids a second options round-trip.
	function distinctOptions(
		values: readonly (string | null | undefined)[],
		active: string
	): string[] {
		const set = new Set<string>();
		for (const value of values) {
			if (value) set.add(value);
		}
		if (active) set.add(active);
		return [...set].sort();
	}

	const statuses = $derived(
		distinctOptions(
			data.items.map((t) => t.status),
			data.filters.status
		)
	);
	const tracks = $derived(
		distinctOptions(
			data.items.map((t) => t.track),
			data.filters.track
		)
	);
	const milestones = $derived(
		distinctOptions(
			data.items.map((t) => t.milestone),
			data.filters.milestone
		)
	);

	// Re-run `+page.ts` with the chosen filters by pushing them onto the URL, so
	// filtering stays server-side and the URL is the source of truth. An empty
	// query resets to `/`.
	function navigate(search: string): void {
		goto(search ? `?${search}` : '?', { keepFocus: true, noScroll: true });
	}
</script>

<div class="space-y-4">
	<h1 class="text-2xl font-semibold text-text">Tickets</h1>

	<FiltersBar filters={data.filters} {statuses} {tracks} {milestones} onNavigate={navigate} />

	{#if data.items.length === 0}
		<p class="rounded-lg border border-slate-200 bg-surface px-4 py-8 text-center text-muted">
			No tickets match — <a class="text-accent hover:underline" href="/">clear filters</a>?
		</p>
	{:else}
		<ul
			class="divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-surface"
		>
			{#each data.items as ticket (ticket.id)}
				<li>
					<TicketRow {ticket} />
				</li>
			{/each}
		</ul>
	{/if}
</div>
