<script lang="ts">
	import { previewWrite, updateTicket } from '$lib/api';
	import { normalizeError, type ApiError } from '$lib/api/contracts';
	import type { Ticket, TicketUpdate, WritePreview } from '$lib/api/models';
	import ApiErrorView from '$lib/components/ApiErrorView.svelte';
	import DiffPreviewModal from '$lib/components/DiffPreviewModal.svelte';
	import ModalShell from '$lib/components/ModalShell.svelte';
	import TicketForm from '$lib/components/TicketForm.svelte';
	import WriteTokenPrompt from '$lib/components/WriteTokenPrompt.svelte';
	import { parseList, serializeList, type TicketFormValues } from '$lib/forms/ticketForm';
	import { clearTokenIfRejected, withWriteToken } from '$lib/stores/writeToken';

	// The whole edit orchestration lives here so `+page.svelte` stays a view:
	// form → dry-run preview → confirm → real write. The route only says when the
	// modal is open and what to do once a write lands.
	let {
		ticket,
		open,
		onClose,
		onSaved
	}: {
		ticket: Ticket;
		open: boolean;
		onClose: () => void;
		/** Called after a write actually applied — the route refreshes its data here. */
		onSaved: () => void;
	} = $props();

	// `ModalShell` only renders its children while open, so `TicketForm` remounts
	// on every open and re-seeds from this snapshot — which is what makes a
	// `$derived` safe here despite the form untracking `initial` internally.
	//
	// `provides` is a SCALAR on the wire, so the form can only hold one entry and
	// seeds from the first (see the contract note in `$lib/forms/ticketForm.ts`).
	// A manifest MAY store a list there, though, and the read model passes it
	// through as-is — see `_provides_to_list` in `file_adapter/manifest.py` — so
	// saving such a ticket rewrites the key to that one entry. The wire contract
	// gives us no way to preserve the rest (`TicketEdit.provides` is a required
	// scalar), so the collapse is surfaced to the user by `dropsProvides` below
	// rather than left to be discovered in the diff. `track`/`milestone` have no
	// form field at all; they are carried through untouched in `toUpdate`.
	const initial = $derived<TicketFormValues>({
		id: ticket.id,
		title: ticket.title,
		dependsOn: serializeList([...(ticket.dependsOn ?? [])]),
		provides: ticket.provides?.[0] ?? '',
		files: serializeList([...(ticket.files ?? [])]),
		body: ticket.bodyMarkdown
	});

	// True when the ticket declares more capabilities than the scalar wire shape
	// can carry back, i.e. saving WILL drop the entries after the first.
	const droppedProvides = $derived((ticket.provides ?? []).slice(1));

	function toUpdate(values: TicketFormValues): TicketUpdate {
		return {
			title: values.title,
			// Not editable in the form — resend what the ticket already has so a save
			// never silently drops them.
			track: ticket.track,
			milestone: ticket.milestone,
			dependsOn: parseList(values.dependsOn),
			provides: values.provides,
			files: parseList(values.files),
			bodyMarkdown: values.body ?? ''
			// `frontMatter` is deliberately omitted: the server overlays the client's
			// keys onto the file's existing front matter, so sending none preserves
			// every field this form cannot edit.
		};
	}

	// The payload the user submitted, held across the token prompt and the diff
	// confirmation — those two steps re-send exactly what was previewed.
	let pending = $state<TicketUpdate | null>(null);
	let needsToken = $state(false);
	let diffOpen = $state(false);
	let preview = $state<WritePreview | null>(null);
	let previewLoading = $state(false);
	let previewError = $state<ApiError | null>(null);
	// A failed real write closes the diff and reports back on the form, where the
	// user can adjust and retry.
	let saveError = $state<ApiError | null>(null);
	let saving = $state(false);
	// Which dry-run the state below belongs to. Bumped by everything that makes an
	// in-flight preview obsolete, so a late response can tell it has been
	// superseded — see `runPreview`.
	let previewRun = 0;

	function reset(): void {
		pending = null;
		needsToken = false;
		diffOpen = false;
		preview = null;
		previewLoading = false;
		previewError = null;
		saveError = null;
		saving = false;
		previewRun += 1;
	}

	async function runPreview(body: TicketUpdate, token: string): Promise<void> {
		// Cancelling the diff keeps the edits, so re-submitting while the first
		// dry-run is still in flight is an ordinary path — and its response would
		// otherwise land on top of the newer one. Only the latest run may write the
		// state the dialog reads: showing run A's diff beside run B's `pending`
		// would make "the exact diff the write would produce" a lie.
		const run = ++previewRun;
		diffOpen = true;
		previewLoading = true;
		previewError = null;
		preview = null;
		try {
			const result = await previewWrite({ verb: 'update', id: ticket.id, body }, token);
			if (run !== previewRun) return;
			preview = result;
		} catch (err) {
			if (run !== previewRun) return;
			const error = normalizeError(err);
			// A rejected token is the one failure the user can fix without leaving:
			// drop it and re-prompt, or every retry re-sends the same bad credential.
			if (clearTokenIfRejected(error)) {
				diffOpen = false;
				needsToken = true;
			}
			previewError = error;
		} finally {
			if (run === previewRun) previewLoading = false;
		}
	}

	function handleSubmit(values: TicketFormValues): void {
		const body = toUpdate(values);
		pending = body;
		saveError = null;
		withWriteToken(
			(token) => void runPreview(body, token),
			// No token, no dry-run: every write verb needs one, so ask first and
			// resume this exact submission from `handleTokenSaved`.
			() => (needsToken = true)
		);
	}

	function handleTokenSaved(): void {
		needsToken = false;
		const body = pending;
		if (body === null) return;
		withWriteToken(
			(token) => void runPreview(body, token),
			() => (needsToken = true)
		);
	}

	async function runSave(body: TicketUpdate, token: string): Promise<void> {
		saving = true;
		try {
			await updateTicket(ticket.id, body, token);
			reset();
			onSaved();
		} catch (err) {
			const error = normalizeError(err);
			if (clearTokenIfRejected(error)) needsToken = true;
			saveError = error;
			diffOpen = false;
		} finally {
			saving = false;
		}
	}

	function handleConfirm(): void {
		// The write is already in flight: a second click would issue a duplicate
		// PUT and a second `onSaved`. The Save button is disabled for the same
		// reason, but the handler owns the guarantee.
		if (saving) return;
		const body = pending;
		if (body === null) return;
		withWriteToken(
			(token) => void runSave(body, token),
			() => {
				// The token can be cleared (or rejected on a 401) between preview and save.
				diffOpen = false;
				needsToken = true;
			}
		);
	}

	// Backing out of the diff returns to the form with the edits intact; nothing
	// was written, so there is nothing to undo. The in-flight dry-run is abandoned
	// with it, so its response must not reopen or repaint anything.
	function handleDiffCancel(): void {
		previewRun += 1;
		diffOpen = false;
		previewLoading = false;
		preview = null;
		previewError = null;
	}

	function handleClose(): void {
		reset();
		onClose();
	}
</script>

<ModalShell
	{open}
	onCancel={handleClose}
	labelledBy="edit-ticket-title"
	panelClass="max-h-[85vh] w-full max-w-2xl overflow-y-auto p-4"
>
	<div class="flex items-baseline justify-between gap-2">
		<h2 id="edit-ticket-title" class="text-base font-semibold text-text">
			Edit {ticket.id}
		</h2>
		<button
			type="button"
			class="rounded border border-slate-300 px-3 py-1 text-sm text-text hover:bg-bg"
			onclick={handleClose}
		>
			Cancel
		</button>
	</div>

	{#if needsToken}
		<div class="mt-3 rounded border border-slate-300 bg-bg p-3">
			<p class="mb-2 text-sm text-text">A write token is required before saving.</p>
			<WriteTokenPrompt onSaved={handleTokenSaved} />
		</div>
	{/if}

	{#if droppedProvides.length}
		<!-- Say it up front rather than let the user find it in the diff: the wire
		     shape holds ONE capability, so saving rewrites the key to the first. -->
		<p class="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
			This ticket declares {droppedProvides.length + 1} capabilities, but a ticket can only be saved with
			one. Saving will drop {droppedProvides.join(', ')}.
		</p>
	{/if}

	{#if saveError}
		<!-- `compact` keeps page chrome out of a dialog body; the action dismisses
		     the message, since retrying means submitting the form again. -->
		<ApiErrorView
			error={saveError}
			compact
			actionLabel="Dismiss"
			onAction={() => (saveError = null)}
		/>
	{/if}

	<div class="mt-3">
		<TicketForm mode="edit" {initial} disabled={saving} onSubmit={handleSubmit} />
	</div>
</ModalShell>

<DiffPreviewModal
	open={diffOpen}
	{preview}
	loading={previewLoading}
	{saving}
	error={previewError}
	onConfirm={handleConfirm}
	onCancel={handleDiffCancel}
/>
