<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { get } from 'svelte/store';
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
	import { clearToken, WRITE_TOKEN_INVALID_CODE, writeToken } from '$lib/stores/writeToken';

	let { data }: { data: PageData } = $props();

	// Plain chip styling for the track/milestone row — mirrors TicketRow's
	// CHIP_CLASS (one complete literal so the Tailwind JIT keeps the classes).
	const CHIP_CLASS = 'rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600';

	// `files` flows straight from the tolerant App-Factory manifest and is never
	// promised to be distinct; a repeated path would be a duplicate `{#each}` key and
	// crash the route with `each_key_duplicate`. De-dup here, first occurrence winning
	// — the same footgun already guarded for chips (ChipList.distinctByLabel) and deps
	// (projection.py's dict.fromkeys) — so the view is safe for ANY manifest.
	const files = $derived([...new Set(data.notFound ? [] : (data.ticket.files ?? []))]);

	// Write affordances. The edit sequence (dry-run → review → apply) lives in
	// `EditTicketModal`; this route owns only the button state, the delete
	// confirmation, and what "written" means for the view it is showing.
	let editOpen = $state(false);
	let confirmDeleteOpen = $state(false);
	let deleteTokenRequested = $state(false);
	let deleting = $state(false);
	let deleteError = $state<ApiError | null>(null);

	// Why the prompt is up. A 401 that silently re-raises the bare prompt looks
	// exactly like never having held a token, so the user re-pastes the SAME
	// rejected value with nothing on screen saying authentication is what failed.
	let deleteTokenRejected = $state(false);

	// The prompt is only for a delete that is WAITING on a token, so it is gated on
	// the store as well as the request: a token pasted into the edit dialog's prompt
	// satisfies this one too, and a panel that stayed up after the token existed
	// would invite a second paste for a delete that no longer needs one.
	const deleteTokenNeeded = $derived(deleteTokenRequested && $writeToken === null);

	// One predicate for both buttons and the banner (`EditGate` calls the same
	// `isEditable`), so what the banner explains is exactly what is disabled. A
	// client-side MIRROR of the server write-gate, never the only gate.
	const canWrite = $derived(!data.notFound && isEditable(data.ticket.runState) && !deleting);

	// SvelteKit REUSES this component instance for a params-only navigation, replacing
	// only `data` — and the "Depends on" chips below link to exactly that (`/tickets/<id>`).
	// The write state above is per-TICKET, so it has to be dropped when the rendered
	// ticket changes: otherwise one ticket's delete error stays on screen attributed to
	// the next, and a left-over token prompt would re-enter `startDelete` and offer to
	// delete a ticket the user never chose.
	let shownId: string | null = null; // plain: written by the effect, never read reactively
	$effect(() => {
		const id = data.notFound ? data.id : data.ticket.id;
		if (id === shownId) {
			return;
		}
		shownId = id;
		editOpen = false;
		confirmDeleteOpen = false;
		deleteTokenRequested = false;
		deleteTokenRejected = false;
		deleting = false;
		deleteError = null;
	});

	const ACTION_CLASS =
		'rounded border border-slate-300 px-3 py-1 text-sm text-text hover:bg-bg disabled:cursor-not-allowed disabled:opacity-60';
	const DANGER_CLASS =
		'rounded border border-red-300 px-3 py-1 text-sm text-danger hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60';

	// Ask for the token BEFORE the confirmation: a dialog whose only outcome could
	// be a 401 is worse than asking for what the write needs up front.
	function startDelete(): void {
		deleteError = null;
		if (get(writeToken) === null) {
			deleteTokenRequested = true;
			return;
		}
		// A token is held again, so whatever the last one was rejected for no longer
		// describes this attempt.
		deleteTokenRequested = false;
		deleteTokenRejected = false;
		confirmDeleteOpen = true;
	}

	// Reconcile the latch with the store. The token can arrive from the OTHER prompt on
	// the page, which does not call `startDelete`; the latch would then stay set forever
	// with no confirmation ever opening, and a LATER `clearToken()` (the edit flow's 401
	// path) would re-raise a delete prompt nobody asked for — pasting into which pops a
	// "Delete ticket?" confirmation for an action abandoned long before.
	//
	// Dropping the latch, NOT resuming into the confirmation, is the answer. Resuming
	// would mean pasting a token to continue an EDIT also throws a destructive dialog on
	// screen — and `ConfirmDialog` renders after the edit modal, so it would paint on top
	// and take focus. A delete the user still wants is one more click on Delete.
	$effect(() => {
		if ($writeToken === null) return;
		if (!deleteTokenRequested) return;
		deleteTokenRequested = false;
		deleteTokenRejected = false;
	});

	// Delete deliberately has NO dry-run step, unlike edit: `ConfirmDialog` already
	// states exactly what is removed, and a diff of a file about to disappear tells
	// the user nothing they can act on. An edit is the case where the diff IS the
	// decision, which is why only that flow goes through `DiffPreviewModal`.
	async function confirmDelete(id: string): Promise<void> {
		// One confirmation is one DELETE. The dialog stays mounted for the whole
		// round-trip, so this guard — not `canWrite`, which only gates the buttons
		// behind the backdrop — is what stops a double-click sending two.
		if (deleting) {
			return;
		}
		const token = get(writeToken);
		if (token === null) {
			// The token was dropped between the prompt and the confirmation.
			confirmDeleteOpen = false;
			deleteTokenRequested = true;
			return;
		}
		deleting = true;
		deleteError = null;
		try {
			await deleteTicket(id, token);
		} catch (err) {
			const apiError = normalizeError(err);
			if (apiError.code === WRITE_TOKEN_INVALID_CODE) {
				// The held token is known bad — the case `clearToken` exists for. Drop it
				// so the prompt comes back; keeping it would leave every retry failing
				// against a rejected credential with no way in the app to replace it.
				clearToken();
				deleteTokenRequested = true;
				deleteTokenRejected = true;
			} else {
				deleteError = apiError;
			}
			deleting = false;
			return;
		} finally {
			confirmDeleteOpen = false;
		}
		// The ticket this route renders is gone, so there is nothing to refresh —
		// leave for the list, forcing its load to re-run so the deleted row is gone.
		// `deleting` is deliberately NOT cleared: the buttons must not re-enable for a
		// ticket that no longer exists while this navigation is still in flight.
		await goto('/', { invalidateAll: true });
	}

	// The write landed on disk, so the loaded ticket is stale: re-run the load rather
	// than patching the view from the response.
	async function handleEditSaved(): Promise<void> {
		editOpen = false;
		await invalidateAll();
	}
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

		<EditGate runState={ticket.runState} />

		<div class="flex flex-wrap items-center gap-2">
			<button
				type="button"
				class={ACTION_CLASS}
				disabled={!canWrite}
				onclick={() => (editOpen = true)}
			>
				Edit
			</button>
			<button type="button" class={DANGER_CLASS} disabled={!canWrite} onclick={startDelete}>
				Delete
			</button>
		</div>

		{#if deleteTokenNeeded}
			<section class="rounded border border-slate-300 bg-bg p-3">
				<h2 class="mb-2 text-sm font-semibold text-text">Write token required</h2>
				{#if deleteTokenRejected}
					<!-- `alert`: unlike the bare prompt this is a failure the user must act
					     on, and the two are otherwise indistinguishable on screen. -->
					<p role="alert" class="mb-2 text-xs text-danger">
						The server rejected the token that was held, so it has been discarded. Paste the
						current one to delete this ticket.
					</p>
				{/if}
				<!-- Saving the token re-enters `startDelete`, which now opens the
				     confirmation — the delete still needs confirming, not just a token. -->
				<WriteTokenPrompt onSaved={startDelete} />
			</section>
		{/if}

		{#if deleteError}
			<!-- `compact` because this sits inside the page, not instead of it, and the
			     action dismisses: the ticket is still here and the delete is retryable. -->
			<ApiErrorView
				error={deleteError}
				compact
				actionLabel="Dismiss"
				onReload={() => (deleteError = null)}
			/>
		{/if}

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

		<EditTicketModal
			{ticket}
			open={editOpen}
			onClose={() => (editOpen = false)}
			onSaved={() => void handleEditSaved()}
		/>

		<ConfirmDialog
			open={confirmDeleteOpen}
			title="Delete ticket?"
			message="This removes {ticket.id} from the manifest and deletes its markdown file. It cannot be undone from here."
			confirmLabel="Delete ticket"
			danger
			busy={deleting}
			onConfirm={() => void confirmDelete(ticket.id)}
			onCancel={() => (confirmDeleteOpen = false)}
		/>
	</div>
{/if}
