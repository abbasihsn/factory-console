<script lang="ts">
	import type { PageData } from './$types';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import RunStateBadge from '$lib/components/RunStateBadge.svelte';
	import MarkdownBody from '$lib/components/MarkdownBody.svelte';
	import ChipList from '$lib/components/ChipList.svelte';

	let { data }: { data: PageData } = $props();

	// Plain chip styling for the track/milestone row — mirrors TicketRow's
	// CHIP_CLASS (one complete literal so the Tailwind JIT keeps the classes).
	const CHIP_CLASS = 'rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600';
</script>

{#if data.notFound}
	<div
		class="mx-auto max-w-lg rounded-lg border border-slate-200 bg-surface px-4 py-16 text-center text-muted"
	>
		<p>Ticket "{data.id}" not found</p>
		<a class="mt-4 inline-block text-accent hover:underline" href="/">back to list</a>
	</div>
{:else}
	{@const ticket = data.ticket}
	<div class="space-y-6">
		<header class="flex flex-wrap items-center gap-3">
			<h1 class="text-2xl font-semibold text-text">{ticket.title}</h1>
			<StatusBadge status={ticket.status} />
			<RunStateBadge runState={ticket.runState} />
		</header>

		{#if ticket.track || ticket.milestone}
			<div class="flex flex-wrap gap-2">
				{#if ticket.track}
					<span class={CHIP_CLASS} title="track">{ticket.track}</span>
				{/if}
				{#if ticket.milestone}
					<span class={CHIP_CLASS} title="milestone">{ticket.milestone}</span>
				{/if}
			</div>
		{/if}

		{#if ticket.dependsOn?.length}
			<section class="space-y-2">
				<h2 class="text-sm font-semibold text-muted">Depends on</h2>
				<ChipList
					items={ticket.dependsOn.map((depId) => ({ label: depId, href: `/tickets/${depId}` }))}
				/>
			</section>
		{/if}

		{#if ticket.provides?.length}
			<section class="space-y-2">
				<h2 class="text-sm font-semibold text-muted">Provides</h2>
				<ChipList items={ticket.provides.map((capability) => ({ label: capability }))} />
			</section>
		{/if}

		{#if ticket.files?.length}
			<section class="space-y-2">
				<h2 class="text-sm font-semibold text-muted">Files</h2>
				<!-- Plain-text paths only: the SPA has no filesystem access, so these are
				     NEVER rendered as `file://` or any other links (REST v1 contract). -->
				<ul class="space-y-1 font-mono text-sm text-text">
					{#each ticket.files as file (file)}
						<li>{file}</li>
					{/each}
				</ul>
			</section>
		{/if}

		<a
			class="inline-block font-medium text-accent hover:underline"
			href="/tickets/{ticket.id}/deps"
		>
			View dep neighborhood →
		</a>

		<MarkdownBody html={ticket.bodyHtml} />
	</div>
{/if}
