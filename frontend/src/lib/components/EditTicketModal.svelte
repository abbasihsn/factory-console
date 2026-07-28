<script lang="ts">
	import { untrack } from 'svelte';
	import { get } from 'svelte/store';
	import { previewWrite, updateTicket } from '$lib/api';
	import type { Ticket, TicketUpdate, WritePreview } from '$lib/api';
	import { normalizeError, type ApiError } from '$lib/api/contracts';
	import DiffPreviewModal from '$lib/components/DiffPreviewModal.svelte';
	import ModalShell from '$lib/components/ModalShell.svelte';
	import TicketForm from '$lib/components/TicketForm.svelte';
	import WriteTokenPrompt from '$lib/components/WriteTokenPrompt.svelte';
	import { serializeList, toTicketUpdate, type TicketFormValues } from '$lib/forms/ticketForm';
	import { clearToken, WRITE_TOKEN_INVALID_CODE, writeToken } from '$lib/stores/writeToken';

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

	// The APPLY specifically, where `busy` covers the dry-run too. Only a write in
	// flight makes the review dialog undismissable: a dry-run writes nothing, so
	// cancelling one is free, while cancelling the PUT would claim to stop something
	// that still lands.
	let applying = $state(false);

	// Which attempt the in-flight request belongs to. A request cannot be recalled, so
	// the only way to make an ABANDONED one harmless is to ignore what it returns: a
	// dry-run whose dialog was closed, or that the ticket changed underneath, must not
	// write `preview` / `writeError` when it finally settles. Without this its failure
	// re-opens the review dialog over whatever the user is doing next — the next click
	// on Edit shows the previous attempt's error before anything was even submitted.
	let attempt = $state(0);

	// Reported when the loaded ticket changes under a review that is on screen. NOT from
	// the server — no write was attempted — but shaped as an `ApiError` so it renders
	// through the same `ApiErrorView` as a real failure instead of inventing a second
	// way to show one.
	const TICKET_CHANGED_ERROR: ApiError = {
		code: 'ticket_changed_on_disk',
		message:
			'This ticket changed on disk, so the reviewed diff no longer describes what would be written. Close and review the edit again.'
	};

	// The body the preview was built from. The apply MUST reuse it verbatim —
	// re-reading the form would let a post-preview keystroke write something the
	// user never reviewed.
	let pendingBody = $state<TicketUpdate | null>(null);

	// What to do once a token exists. Holding the ACTION (not a token we don't have)
	// is what lets one detour serve both the dry-run and the apply.
	let pendingAction = $state<(() => void) | null>(null);

	// Gated on the STORE as well as the parked action, like the detail route's
	// sibling panel: both prompts can be on screen at once, and a token pasted into
	// the other one satisfies this action too. Without the store in the predicate
	// this dialog would go on demanding a token it already has.
	const tokenNeeded = $derived(pendingAction !== null && $writeToken === null);

	// Why the prompt is up. Raising it silently after a 401 is indistinguishable from
	// never having held a token, so the user re-pastes the SAME rejected value and
	// watches it fail again with nothing on screen saying authentication is what
	// failed. Only the rejection path sets this; the plain no-token detour leaves it
	// false.
	let tokenRejected = $state(false);

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
	 * Run `action` with this session's write token, or park it behind the prompt.
	 *
	 * The token is read imperatively at click time via `get` rather than through a
	 * `$writeToken` subscription: the value that matters is the one held when the
	 * request goes out, and reading it here also makes the resume path (below)
	 * pick up the token the prompt just stored with no ordering subtlety.
	 *
	 * Closes the review dialog before parking, same as the 401 branch in
	 * {@link handleWriteFailure}: the token prompt renders in the FORM dialog, behind
	 * the review dialog's backdrop and outside its focus trap, so parking behind a
	 * still-open review dialog raises a prompt nobody can reach and leaves Save
	 * looking live while it silently does nothing. `handleConfirm` is the one caller
	 * that can reach here with the review dialog open — the token can go missing
	 * between opening it and clicking Save, since the sibling delete flow on the same
	 * route clears the store on its own 401. A no-op for the other callers, which
	 * either have no dialog open yet or already closed it themselves.
	 */
	function withToken(action: (token: string) => void): void {
		const token = get(writeToken);
		if (token === null) {
			previewOpen = false;
			pendingAction = () => withToken(action);
			return;
		}
		pendingAction = null;
		action(token);
	}

	// Resumption is driven off the STORE, not off one prompt's `onSaved`, because the
	// token can arrive from the OTHER prompt on the page — the route raises its own
	// for delete, and it knows nothing about the edit parked here. Watching the store
	// is the one mechanism that covers both. `withToken` clears `pendingAction` before
	// it runs the action, so a resumed action cannot be resumed twice.
	$effect(() => {
		if ($writeToken === null) return;
		const resume = pendingAction;
		if (resume === null) return;
		untrack(resume);
	});

	function handleSubmit(values: TicketFormValues): void {
		// A fresh submit is a fresh attempt: whatever a previous one was rejected for
		// no longer describes this one.
		tokenRejected = false;
		const body = toTicketUpdate(values, ticket);
		pendingBody = body;
		withToken((token) => void runPreview(body, token));
	}

	/**
	 * Route a failed write: a REJECTED TOKEN is not a terminal error.
	 *
	 * A 401 means the token we hold is known bad — it is the case
	 * {@link clearToken} exists for. Dropping it and re-parking `retry` behind the
	 * prompt is what lets the user paste the current token and have the write
	 * resume; leaving it stored would make every further attempt fail against a
	 * credential already rejected, with nothing in the app to replace it (the
	 * prompts only appear when NO token is held).
	 *
	 * The review dialog is closed first because the prompt renders inside the form
	 * dialog, which sits behind that dialog's backdrop and outside its focus trap —
	 * leaving it open would show a prompt nobody can click or tab to.
	 */
	function handleWriteFailure(err: unknown, retry: (token: string) => void): void {
		const apiError = normalizeError(err);
		if (apiError.code !== WRITE_TOKEN_INVALID_CODE) {
			writeError = apiError;
			// The review dialog is the ONLY place this component renders a write error,
			// so the error state is worth nothing while that dialog is closed. An apply
			// that fails after a token re-entry is exactly that case — the 401 branch
			// below already tore the dialog down — and without reopening it the write
			// would fail in complete silence, leaving the edit looking applied.
			// Re-asserting `true` is a no-op on every path where it is already open.
			previewOpen = true;
			return;
		}
		clearToken();
		previewOpen = false;
		preview = null;
		writeError = null;
		tokenRejected = true;
		// The token is null now, so this parks `retry` and raises the prompt.
		withToken(retry);
	}

	async function runPreview(body: TicketUpdate, token: string): Promise<void> {
		// Open the review dialog up front so the spinner (and any failure) is shown
		// where the diff will be, rather than leaving the form looking idle.
		previewOpen = true;
		preview = null;
		writeError = null;
		busy = true;
		const seq = attempt;
		try {
			const result = await previewWrite({ verb: 'update', id: ticket.id, body }, token);
			if (seq !== attempt) return;
			preview = result;
		} catch (err) {
			if (seq !== attempt) return;
			handleWriteFailure(err, (retryToken) => void runPreview(body, retryToken));
		} finally {
			if (seq === attempt) busy = false;
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
		applying = true;
		const seq = attempt;
		try {
			await updateTicket(ticket.id, body, token);
		} catch (err) {
			if (seq !== attempt) return;
			// Nothing was written, so the dialog stays open on the failure rather than
			// closing over it. A rejected token re-raises the prompt and resumes THIS
			// body once a good one is pasted; any other failure is reported on the
			// review dialog, from which Cancel returns to the form with the edits intact.
			handleWriteFailure(err, (retryToken) => void applyEdit(body, retryToken));
			return;
		} finally {
			busy = false;
			applying = false;
		}
		if (seq !== attempt) return;
		resetWriteState();
		onSaved();
	}

	// Also SUPERSEDES anything in flight: whatever comes back for the attempt being
	// abandoned here must not write its result into the state this just cleared. And
	// since an abandoned dry-run's `finally` will now decline to clear `busy`, it is
	// cleared here — otherwise the form stays disabled with no request to wait for.
	function resetWriteState(): void {
		attempt += 1;
		previewOpen = false;
		preview = null;
		pendingBody = null;
		pendingAction = null;
		writeError = null;
		tokenRejected = false;
		busy = false;
	}

	// What the reviewed diff is BASED on — every field that feeds `toTicketUpdate`,
	// plus the body. The reset below keys on this rather than on `ticket.id` alone.
	//
	// An id-only guard misses the more dangerous case: the root layout `invalidateAll()`s
	// on every SSE bump, which re-runs the detail load and replaces `ticket` IN PLACE
	// with the SAME id. `preview` then still shows a diff computed against content that
	// has since changed on disk, and `pendingBody` was built from that stale content —
	// so confirming overwrites the concurrent change with a body whose effect the user
	// was never shown. Nothing downstream catches it: the server's edit path checks
	// existence only, with no version or mtime check.
	const editBasis = $derived(
		JSON.stringify([
			ticket.id,
			ticket.title,
			ticket.track,
			ticket.milestone,
			ticket.dependsOn ?? [],
			ticket.provides ?? [],
			ticket.files ?? [],
			ticket.bodyMarkdown
		])
	);

	// The `ticket` being edited can be REPLACED under this component: the detail route
	// reuses its instance across a params-only navigation, and it closes the dialog by
	// flipping `open` — which does NOT run `handleClose`, the only other caller of
	// `resetWriteState`. Everything that reset clears is per-ticket, and `DiffPreviewModal`
	// below is gated on `previewOpen` alone, so without this a diff reviewed for one
	// ticket could survive and be applied to ANOTHER (`applyEdit` sends the current
	// `ticket.id`). Dropping the reviewed diff is the only safe answer: it describes a
	// write against content that is no longer what is loaded. The typed form is left
	// alone — re-submitting runs a fresh dry-run against the current content, which is
	// where the user sees what the write would now do.
	let reviewedBasis: string | null = null; // plain: written by the effect, never read reactively
	$effect(() => {
		const basis = editBasis;
		// Never reset across a request in flight — `busy` covers the dry-run, `applying`
		// the write. An SSE bump can land mid-request, and resetting then would either
		// close the dialog the apply is running under (the dismissal
		// `handlePreviewCancel` refuses) or wipe the very error the settling request is
		// about to report. Both are `$state`, so this effect re-runs and resets, with
		// the basis re-checked, once the request settles.
		if (applying || busy) {
			return;
		}
		if (basis === reviewedBasis) {
			return;
		}
		const wasReviewing = previewOpen;
		// A write parked behind the token prompt (`withToken` returned without
		// starting a request) hits neither `applying` nor `busy` above, so without
		// this the reset below drops `pendingAction`/`pendingBody` with nothing on
		// screen saying so — pasting the token then does nothing.
		const wasParked = pendingAction !== null;
		reviewedBasis = basis;
		resetWriteState();
		// Gated on `open`: this component stays mounted across the host closing, so
		// surfacing the error here while closed would leave it stale on `previewOpen`
		// and paint over the next Edit before anything is even submitted.
		if (open && (wasReviewing || wasParked)) {
			// The user was looking at that diff (or waiting on a token for it), so it
			// must not simply vanish. Keep the dialog up and say why it went away. Save
			// stays inert: `preview` and `pendingBody` are gone, so nothing stale can be
			// applied from here — only Close, then a fresh dry-run against the new content.
			writeError = TICKET_CHANGED_ERROR;
			previewOpen = true;
		}
	});

	// Cancelling the review returns to the form with the edits intact — the form
	// stays mounted underneath precisely so nothing typed is lost here. Refused while
	// the apply is in flight: that write cannot be called off, and dismissing over it
	// would report a cancellation for an edit that still lands.
	function handlePreviewCancel(): void {
		if (applying) return;
		// Supersede whatever dry-run is in flight, same as `resetWriteState`: without
		// this, a cancelled-but-still-settling dry-run's `finally`/`catch` still write
		// into `preview`/`writeError`/`previewOpen` for a dialog the user just closed.
		// `busy` is cleared directly rather than via `resetWriteState()` because that
		// request's own `finally` will now decline to clear it (its `seq` no longer
		// matches `attempt`), and nothing else would.
		attempt += 1;
		busy = false;
		previewOpen = false;
		preview = null;
		pendingBody = null;
		writeError = null;
	}

	// Closing the dialog writes nothing. The in-progress edit is dropped with it:
	// `ModalShell` only instantiates its body while open, so the next open reseeds
	// `TicketForm` from the ticket as loaded.
	//
	// Refused mid-apply for the same reason as `handlePreviewCancel`: every control in
	// the review dialog is disabled then, so focus can fall out to THIS dialog's Close,
	// and closing here would drop the pending body while the PUT is still in flight —
	// leaving a later failure to reopen a review dialog whose host is gone.
	function handleClose(): void {
		if (applying) return;
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
				{#if tokenRejected}
					<!-- `alert`: this one IS a failure the user has to act on, and it
					     replaces a prompt that would otherwise look identical to the
					     plain "no token yet" case. -->
					<p role="alert" class="mb-2 text-xs text-danger">
						The server rejected the token that was held, so it has been discarded. Paste the current
						one to continue — your edit is still here.
					</p>
				{/if}
				<WriteTokenPrompt />
			</section>
		{/if}

		<!-- Keyed on the ticket so a REPLACED one reseeds the fields. `TicketForm`
		     snapshots `initial` exactly once (deliberately, so a later prop change
		     cannot clobber in-progress typing), which without this key would leave
		     ticket A's values in a dialog now headed and applied as ticket B. -->
		{#key ticket.id}
			<TicketForm mode="edit" initial={initialValues} disabled={busy} onSubmit={handleSubmit} />
		{/key}
	</div>
</ModalShell>

<!-- Stacked over the form dialog, which stays mounted so Cancel comes back to the
     in-progress edit. `busy={applying}` (not `loading`) is what keeps Save/Cancel inert
     during the apply; `loading` reflects only the dry-run (`busy && !applying`) so the
     spinner does not paint over the confirmed diff while the write itself is running. -->
<!-- Gated on `open` too: the review dialog is stacked ON the form dialog, so it must
     never outlive its host and render parentless over the detail page. -->
<DiffPreviewModal
	open={previewOpen && open}
	{preview}
	loading={busy && !applying}
	busy={applying}
	error={writeError}
	onConfirm={handleConfirm}
	onCancel={handlePreviewCancel}
/>
