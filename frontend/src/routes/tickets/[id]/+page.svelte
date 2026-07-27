<script lang="ts">
	import { get } from 'svelte/store';
	import { goto, invalidateAll } from '$app/navigation';
	import type { PageData } from './$types';
	import { deleteTicket } from '$lib/api';
	import { normalizeError, type ApiError } from '$lib/api/contracts';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import RunStateBadge from '$lib/components/RunStateBadge.svelte';
	import MarkdownBody from '$lib/components/MarkdownBody.svelte';
	import ChipList from '$lib/components/ChipList.svelte';
	import ApiErrorView from '$lib/components/ApiErrorView.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import EditGate from '$lib/components/EditGate.svelte';
	import EditTicketModal from '$lib/components/EditTicketModal.svelte';
	import WriteTokenPrompt from '$lib/components/WriteTokenPrompt.svelte';
	import { isEditable } from '$lib/forms/editability';
	import { writeToken } from '$lib/stores/writeToken';

	let { data }: { data: PageData } = $props();

	// Edit orchestration (form → dry-run → confirm → PUT) lives in
	// `EditTicketModal`; this route only opens it and refreshes afterwards. Delete
	// has no form and no dry-run — confirm, DELETE, leave — so it stays here.
	let editOpen = $state(false);
	let confirmDeleteOpen = $state(false);
	let deleteNeedsToken = $state(false);
	let deleteError = $state<ApiError | null>(null);

	async function runDelete(id: string, token: string): Promise<void> {
		try {
			await deleteTicket(id, token);
			// The ticket this route renders is gone — go back to the list rather
			// than re-fetching a 404.
			await goto('/');
		} catch (err) {
			deleteError = normalizeError(err);
		}
	}

	function handleDeleteConfirm(id: string): void {
		confirmDeleteOpen = false;
		deleteError = null;
		const token = get(writeToken);
		if (!token) {
			// No token, no write: ask for one and resume the already-confirmed
			// delete from `handleDeleteTokenSaved`.
			deleteNeedsToken = true;
			return;
		}
		void runDelete(id, token);
	}

	function handleDeleteTokenSaved(id: string): void {
		deleteNeedsToken = false;
		const token = get(writeToken);
		if (token === null) return;
		void runDelete(id, token);
	}

	// Plain chip styling for the track/milestone row — mirrors TicketRow's
	// CHIP_CLASS (one complete literal so the Tailwind JIT keeps the classes).
	const CHIP_CLASS = 'rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600';

	// Shared styling for the Edit/Delete pair — one complete literal so the
	// Tailwind JIT keeps the classes, including the gated (disabled) look.
	const ACTION_CLASS =
		'rounded border border-slate-300 px-3 py-1 text-sm text-text hover:bg-bg disabled:cursor-not-allowed disabled:opacity-60';

	// `files` flows straight from the tolerant App-Factory manifest and is never
	// promised to be distinct; a repeated path would be a duplicate `{#each}` key and
	// crash the route with `each_key_duplicate`. De-dup here, first occurrence winning
	// — the same footgun already guarded for chips (ChipList.distinctByLabel) and deps
	// (projection.py's dict.fromkeys) — so the view is safe for ANY manifest.
	const files = $derived([...new Set(data.notFound ? [] : (data.ticket.files ?? []))]);
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

		<!-- The client-side mirror of the server write-gate: the banner explains
		     why the two buttons below it are inert, and both come from the same
		     `isEditable` predicate. Never the sole gate — the server re-checks. -->
		<EditGate runState={ticket.runState} />

		<div class="flex flex-wrap items-center gap-2">
			<button
				type="button"
				class={ACTION_CLASS}
				disabled={!isEditable(ticket.runState)}
				onclick={() => (editOpen = true)}
			>
				Edit
			</button>
			<button
				type="button"
				class="{ACTION_CLASS} text-danger"
				disabled={!isEditable(ticket.runState)}
				onclick={() => (confirmDeleteOpen = true)}
			>
				Delete
			</button>
		</div>

		{#if deleteNeedsToken}
			<div class="rounded border border-slate-300 bg-bg p-3">
				<p class="mb-2 text-sm text-text">A write token is required before deleting.</p>
				<WriteTokenPrompt onSaved={() => handleDeleteTokenSaved(ticket.id)} />
			</div>
		{/if}

		{#if deleteError}
			<!-- Inline and compact: a failed delete leaves the user on the ticket
			     they were reading, not in the route's error boundary. -->
			<ApiErrorView
				error={deleteError}
				compact
				actionLabel="Dismiss"
				onReload={() => (deleteError = null)}
			/>
		{/if}

		<EditTicketModal
			{ticket}
			open={editOpen}
			onClose={() => (editOpen = false)}
			onSaved={() => {
				editOpen = false;
				void invalidateAll();
			}}
		/>

		<ConfirmDialog
			open={confirmDeleteOpen}
			title="Delete {ticket.id}?"
			message="This removes the ticket file and its manifest entry. This cannot be undone from the console."
			confirmLabel="Delete"
			danger
			onConfirm={() => handleDeleteConfirm(ticket.id)}
			onCancel={() => (confirmDeleteOpen = false)}
		/>

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

		{#if files.length}
			<section class="space-y-2">
				<h2 class="text-sm font-semibold text-muted">Files</h2>
				<!-- Plain-text paths only: the SPA has no filesystem access, so these are
				     NEVER rendered as `file://` or any other links (REST v1 contract).
				     `files` is de-duplicated in the script (see above). -->
				<ul class="space-y-1 font-mono text-sm text-text">
					{#each files as file (file)}
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
