<script lang="ts">
	import type { PageData } from './$types';
	import TicketMiniRow from '$lib/components/TicketMiniRow.svelte';

	let { data }: { data: PageData } = $props();
</script>

{#if data.notFound}
	<div
		class="mx-auto max-w-lg rounded-lg border border-slate-200 bg-surface px-4 py-16 text-center text-muted"
	>
		<p>Ticket "{data.id}" not found</p>
		<a class="mt-4 inline-block text-accent hover:underline" href="/">back to list</a>
	</div>
{:else}
	{@const dep = data.deps}
	<div class="space-y-6">
		<header class="space-y-1">
			<a class="inline-block text-sm text-accent hover:underline" href="/tickets/{dep.ticket.id}">
				← back to ticket
			</a>
			<h1 class="text-2xl font-semibold text-text">Deps for {dep.ticket.id}</h1>
		</header>

		<section class="space-y-2">
			<h2 class="text-sm font-semibold text-muted">Depends on</h2>
			{#if (dep.directDeps ?? []).length}
				<ul
					class="divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-surface"
				>
					{#each dep.directDeps ?? [] as ticket (ticket.id)}
						<li>
							<TicketMiniRow {ticket} />
						</li>
					{/each}
				</ul>
			{:else}
				<p class="text-sm text-muted">None</p>
			{/if}
		</section>

		<section class="space-y-2">
			<h2 class="text-sm font-semibold text-muted">Depended on by</h2>
			{#if (dep.directDependents ?? []).length}
				<ul
					class="divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-surface"
				>
					{#each dep.directDependents ?? [] as ticket (ticket.id)}
						<li>
							<TicketMiniRow {ticket} />
						</li>
					{/each}
				</ul>
			{:else}
				<p class="text-sm text-muted">None</p>
			{/if}
		</section>

		<section class="space-y-2">
			<h2 class="text-sm font-semibold text-muted">Unresolved deps</h2>
			{#if (dep.unresolvedDeps ?? []).length}
				<!-- Plain-text ids only: the backend could NOT resolve these to tickets,
				     so they are NEVER rendered as links (they have no detail page). -->
				<ul class="space-y-1 font-mono text-sm text-text">
					{#each dep.unresolvedDeps ?? [] as depId (depId)}
						<li>{depId}</li>
					{/each}
				</ul>
			{:else}
				<p class="text-sm text-muted">None</p>
			{/if}
		</section>
	</div>
{/if}
