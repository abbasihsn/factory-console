<script lang="ts">
	import { get } from 'svelte/store';
	import { previewWrite, updateTicket } from '$lib/api';
	import { normalizeError, type ApiError } from '$lib/api/contracts';
	import type { Ticket, TicketUpdate, WritePreview } from '$lib/api/models';
	import ApiErrorView from '$lib/components/ApiErrorView.svelte';
	import DiffPreviewModal from '$lib/components/DiffPreviewModal.svelte';
	import ModalShell from '$lib/components/ModalShell.svelte';
	import TicketForm from '$lib/components/TicketForm.svelte';
	import WriteTokenPrompt from '$lib/components/WriteTokenPrompt.svelte';
	import { parseList, serializeList, type TicketFormValues } from '$lib/forms/ticketForm';
	import { writeToken } from '$lib/stores/writeToken';

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
	// `provides` is a SCALAR on the wire but comes back from the read model as a
	// single-element list, so it collapses back to its first entry here (see the
	// contract note in `$lib/forms/ticketForm.ts`). `track`/`milestone` have no
	// form field at all; they are carried through untouched in `toUpdate`.
	const initial = $derived<TicketFormValues>({
		id: ticket.id,
		title: ticket.title,
		dependsOn: serializeList([...(ticket.dependsOn ?? [])]),
		provides: ticket.provides?.[0] ?? '',
		files: serializeList([...(ticket.files ?? [])]),
		body: ticket.bodyMarkdown
	});

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

	function reset(): void {
		pending = null;
		needsToken = false;
		diffOpen = false;
		preview = null;
		previewLoading = false;
		previewError = null;
		saveError = null;
		saving = false;
	}

	async function runPreview(body: TicketUpdate, token: string): Promise<void> {
		diffOpen = true;
		previewLoading = true;
		previewError = null;
		preview = null;
		try {
			preview = await previewWrite({ verb: 'update', id: ticket.id, body }, token);
		} catch (err) {
			previewError = normalizeError(err);
		} finally {
			previewLoading = false;
		}
	}

	function handleSubmit(values: TicketFormValues): void {
		const body = toUpdate(values);
		pending = body;
		saveError = null;
		const token = get(writeToken);
		if (!token) {
			// No token, no dry-run: every write verb needs one, so ask first and
			// resume this exact submission from `handleTokenSaved`.
			needsToken = true;
			return;
		}
		void runPreview(body, token);
	}

	function handleTokenSaved(): void {
		needsToken = false;
		const token = get(writeToken);
		if (token === null || pending === null) return;
		void runPreview(pending, token);
	}

	async function handleConfirm(): Promise<void> {
		const token = get(writeToken);
		if (pending === null) return;
		if (!token) {
			// The token can be cleared (or expire on a 401) between preview and save.
			diffOpen = false;
			needsToken = true;
			return;
		}
		saving = true;
		try {
			await updateTicket(ticket.id, pending, token);
			reset();
			onSaved();
		} catch (err) {
			saveError = normalizeError(err);
			diffOpen = false;
		} finally {
			saving = false;
		}
	}

	// Backing out of the diff returns to the form with the edits intact; nothing
	// was written, so there is nothing to undo.
	function handleDiffCancel(): void {
		diffOpen = false;
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

	{#if saveError}
		<!-- `compact` keeps page chrome out of a dialog body; the action dismisses
		     the message, since retrying means submitting the form again. -->
		<ApiErrorView
			error={saveError}
			compact
			actionLabel="Dismiss"
			onReload={() => (saveError = null)}
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
	error={previewError}
	onConfirm={handleConfirm}
	onCancel={handleDiffCancel}
/>
