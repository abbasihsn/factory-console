<script lang="ts">
	import { get } from 'svelte/store';
	import { previewWrite, updateTicket } from '$lib/api';
	import type { Ticket, TicketUpdate, WritePreview } from '$lib/api';
	import { normalizeError, type ApiError } from '$lib/api/contracts';
	import DiffPreviewModal from '$lib/components/DiffPreviewModal.svelte';
	import ModalShell from '$lib/components/ModalShell.svelte';
	import TicketForm from '$lib/components/TicketForm.svelte';
	import WriteTokenPrompt from '$lib/components/WriteTokenPrompt.svelte';
	import { parseList, serializeList, type TicketFormValues } from '$lib/forms/ticketForm';
	import { writeToken } from '$lib/stores/writeToken';

	// The edit ORCHESTRATOR: it owns the whole form → dry-run → confirm → apply
	// sequence (and the missing-token detour) so the detail route only has to mount
	// it and say what "saved" means. Everything it hosts stays presentational.
	//
	// This is the one piece of the flow that talks to the API, so it is also the one
	// piece a test has to mock `$lib/api` for.
	let {
		ticket,
		open,
		onClose,
		onSaved
	}: {
		ticket: Ticket;
		open: boolean;
		/** Dismissed without writing — the host closes the dialog. */
		onClose: () => void;
		/** The PUT was applied — the host closes the dialog and refreshes its data. */
		onSaved: () => void;
	} = $props();

	// The dry-run result being reviewed, plus the flags `DiffPreviewModal` renders
	// from. `busy` covers BOTH the dry-run and the apply, which is what keeps its
	// Save button inert while either request is in flight (no double-apply).
	let previewOpen = $state(false);
	let preview = $state<WritePreview | null>(null);
	let busy = $state(false);
	let writeError = $state<ApiError | null>(null);

	// The body the preview was built from. The apply MUST reuse it verbatim —
	// re-reading the form would let a post-preview keystroke write something the
	// user never reviewed.
	let pendingBody = $state<TicketUpdate | null>(null);

	// What to do once a token exists. Holding the ACTION (not a token we don't have)
	// is what lets one detour serve both the dry-run and the apply.
	let pendingAction = $state<(() => void) | null>(null);
	const tokenNeeded = $derived(pendingAction !== null);

	// Seeded from the loaded ticket. `dependsOn` / `files` are the two list fields
	// the form edits as newline text; `provides` is a SCALAR on the wire, and the
	// read model wraps the stored scalar as a single-element list — so joining is an
	// identity round-trip for it, while a manifest that stored a real list still
	// shows every value here instead of silently dropping all but the first.
	const initialValues = $derived<TicketFormValues>({
		id: ticket.id,
		title: ticket.title,
		dependsOn: serializeList([...(ticket.dependsOn ?? [])]),
		provides: (ticket.provides ?? []).join(', '),
		files: serializeList([...(ticket.files ?? [])]),
		body: ticket.bodyMarkdown
	});

	/**
	 * The PUT body for one set of form values.
	 *
	 * `track` / `milestone` have no form field, but a PUT REPLACES every field the
	 * server treats as editable: omitting them writes `null` over the manifest
	 * entry's real values. Echoing what was loaded makes an ordinary edit preserve
	 * them, and a ticket that genuinely has neither still sends the explicit `null`
	 * that means "none".
	 */
	function toUpdate(values: TicketFormValues): TicketUpdate {
		return {
			title: values.title.trim(),
			track: ticket.track ?? null,
			milestone: ticket.milestone ?? null,
			dependsOn: parseList(values.dependsOn),
			provides: values.provides.trim(),
			files: parseList(values.files),
			bodyMarkdown: values.body ?? ''
		};
	}

	/**
	 * Run `action` with this session's write token, or park it behind the prompt.
	 *
	 * The token is read imperatively at click time via `get` rather than through a
	 * `$writeToken` subscription: the value that matters is the one held when the
	 * request goes out, and reading it here also makes the resume path (below)
	 * pick up the token the prompt just stored with no ordering subtlety.
	 */
	function withToken(action: (token: string) => void): void {
		const token = get(writeToken);
		if (token === null) {
			pendingAction = () => withToken(action);
			return;
		}
		pendingAction = null;
		action(token);
	}

	function handleTokenSaved(): void {
		// `withToken` re-parks itself if the stored token is somehow still absent, so
		// this cannot fall through into an unauthenticated request.
		pendingAction?.();
	}

	function handleSubmit(values: TicketFormValues): void {
		const body = toUpdate(values);
		pendingBody = body;
		withToken((token) => void runPreview(body, token));
	}

	async function runPreview(body: TicketUpdate, token: string): Promise<void> {
		// Open the review dialog up front so the spinner (and any failure) is shown
		// where the diff will be, rather than leaving the form looking idle.
		previewOpen = true;
		preview = null;
		writeError = null;
		busy = true;
		try {
			preview = await previewWrite({ verb: 'update', id: ticket.id, body }, token);
		} catch (err) {
			writeError = normalizeError(err);
		} finally {
			busy = false;
		}
	}

	function handleConfirm(): void {
		const body = pendingBody;
		// Save is disabled without a preview, but never apply a body nobody reviewed.
		if (body === null) return;
		withToken((token) => void applyEdit(body, token));
	}

	async function applyEdit(body: TicketUpdate, token: string): Promise<void> {
		writeError = null;
		busy = true;
		try {
			await updateTicket(ticket.id, body, token);
		} catch (err) {
			// Stay open on the reviewed diff: the edit is intact and retryable.
			writeError = normalizeError(err);
			return;
		} finally {
			busy = false;
		}
		resetWriteState();
		onSaved();
	}

	function resetWriteState(): void {
		previewOpen = false;
		preview = null;
		pendingBody = null;
		pendingAction = null;
		writeError = null;
	}

	// Cancelling the review returns to the form with the edits intact — the form
	// stays mounted underneath precisely so nothing typed is lost here.
	function handlePreviewCancel(): void {
		previewOpen = false;
		preview = null;
		pendingBody = null;
		writeError = null;
	}

	// Closing the dialog writes nothing. The in-progress edit is dropped with it:
	// `ModalShell` only instantiates its body while open, so the next open reseeds
	// `TicketForm` from the ticket as loaded.
	function handleClose(): void {
		resetWriteState();
		onClose();
	}
</script>

<ModalShell
	{open}
	onCancel={handleClose}
	labelledBy="edit-ticket-title"
	panelClass="flex max-h-[85vh] w-full max-w-2xl flex-col"
>
	<div class="flex items-baseline justify-between gap-2 border-b border-slate-300 px-4 py-3">
		<h2 id="edit-ticket-title" class="text-base font-semibold text-text">
			Edit <span class="font-mono">{ticket.id}</span>
		</h2>
		<!-- "Close", not "Cancel": the review dialog stacked on top owns "Cancel"
		     (which returns HERE), so two buttons reading the same word would be
		     ambiguous the moment both are on screen. -->
		<button
			type="button"
			class="rounded border border-slate-300 px-3 py-1 text-sm text-text hover:bg-bg"
			onclick={handleClose}
		>
			Close
		</button>
	</div>

	<div class="flex-1 overflow-y-auto px-4 py-3">
		{#if tokenNeeded}
			<!-- Rendered ABOVE the form rather than in place of it: the edit that
			     triggered this is still held and resumes on save, so replacing the
			     form would discard the values the user just submitted. -->
			<section class="mb-4 rounded border border-slate-300 bg-bg p-3">
				<h3 class="mb-2 text-sm font-semibold text-text">Write token required</h3>
				<WriteTokenPrompt onSaved={handleTokenSaved} />
			</section>
		{/if}

		<TicketForm mode="edit" initial={initialValues} disabled={busy} onSubmit={handleSubmit} />
	</div>
</ModalShell>

<!-- Stacked over the form dialog, which stays mounted so Cancel comes back to the
     in-progress edit. `loading` covers the apply too, so Save cannot fire twice. -->
<DiffPreviewModal
	open={previewOpen}
	{preview}
	loading={busy}
	error={writeError}
	onConfirm={handleConfirm}
	onCancel={handlePreviewCancel}
/>
