<script lang="ts">
	import { untrack } from 'svelte';
	import { get } from 'svelte/store';
	import { goto } from '$app/navigation';
	import type { PageData } from './$types';
	import { createTicket, previewWrite } from '$lib/api';
	import type { TicketCreate, WritePreview } from '$lib/api';
	import { normalizeError, type ApiError } from '$lib/api/contracts';
	import DiffPreviewModal from '$lib/components/DiffPreviewModal.svelte';
	import TicketForm from '$lib/components/TicketForm.svelte';
	import WriteTokenPrompt from '$lib/components/WriteTokenPrompt.svelte';
	import { toTicketCreate, type TicketFormValues } from '$lib/forms/ticketForm';
	import { clearToken, WRITE_TOKEN_INVALID_CODE, writeToken } from '$lib/stores/writeToken';

	// The create ORCHESTRATOR, as a real route rather than a modal: it owns the whole
	// form → dry-run → confirm → apply sequence (and the missing-token detour), the one
	// place on this page that talks to the API — so it is the one thing a test mocks
	// `$lib/api` for.
	//
	// It is deliberately the SIMPLER sibling of `EditTicketModal`: there is no existing
	// ticket, so none of that component's "the ticket changed on disk under a review"
	// machinery (the `editBasis` effect, `TICKET_CHANGED_ERROR`, the reseed-on-replace
	// key) has anything to guard here. A create previews content that exists only in the
	// form, and a fresh navigation mounts a fresh page — so this is just: park writes
	// behind the token prompt, dry-run, review, apply, navigate.
	let { data }: { data: PageData } = $props();

	// The dry-run result being reviewed plus the flags `DiffPreviewModal` renders from.
	// `busy` covers BOTH the dry-run and the apply, keeping Save inert while either is in
	// flight (no double-apply); `applying` is the apply specifically, which is what makes
	// the review dialog undismissable — a dry-run writes nothing, so cancelling one is
	// free, while cancelling the POST would claim to stop a create that still lands.
	let previewOpen = $state(false);
	let preview = $state<WritePreview | null>(null);
	let busy = $state(false);
	let applying = $state(false);
	let writeError = $state<ApiError | null>(null);

	// Which attempt an in-flight request belongs to. A request cannot be recalled, so the
	// only way to make an ABANDONED one harmless is to ignore what it returns: a dry-run
	// whose review dialog was cancelled must not re-open it when it finally settles.
	let attempt = $state(0);

	// The body the preview was built from. The apply MUST reuse it verbatim — re-deriving
	// it from the form would let a post-preview keystroke create something the user never
	// reviewed.
	let pendingBody = $state<TicketCreate | null>(null);

	// What to do once a token exists. Holding the ACTION (not a token we don't have) is
	// what lets one detour serve both the dry-run and the apply.
	let pendingAction = $state<(() => void) | null>(null);

	// Gated on the STORE as well as the parked action: a token pasted elsewhere satisfies
	// this action too, and without the store in the predicate the prompt would go on
	// demanding a token it already has.
	const tokenNeeded = $derived(pendingAction !== null && $writeToken === null);

	// Why the prompt is up. Raising it silently after a 401 is indistinguishable from
	// never having held a token, so the user re-pastes the SAME rejected value and watches
	// it fail again with nothing saying authentication is what failed. Only the rejection
	// path sets this; the plain no-token detour leaves it false.
	let tokenRejected = $state(false);

	/**
	 * Run `action` with this session's write token, or park it behind the prompt.
	 *
	 * The token is read imperatively at click time via `get` rather than through a
	 * `$writeToken` subscription: the value that matters is the one held when the request
	 * goes out, and reading it here also lets the resume effect below pick up the token
	 * the prompt just stored with no ordering subtlety.
	 *
	 * Closes the review dialog before parking, mirroring the 401 branch in
	 * {@link handleWriteFailure}: the token prompt renders in the PAGE body, behind the
	 * review dialog's backdrop and outside its focus trap, so parking behind a still-open
	 * review dialog raises a prompt nobody can reach while Save looks live but does
	 * nothing. `handleConfirm` is the caller that can reach here with the dialog open — the
	 * token can go missing between opening it and clicking Save. A no-op for the submit
	 * caller, which has no dialog open yet.
	 *
	 * Also where `tokenRejected` is cleared — only once a token is actually held, not on
	 * every submit regardless of outcome, so parking behind a still-missing token cannot
	 * silently erase the "rejected" explanation the prompt just raised.
	 */
	function withToken(action: (token: string) => void): void {
		const token = get(writeToken);
		if (token === null) {
			previewOpen = false;
			pendingAction = () => withToken(action);
			return;
		}
		pendingAction = null;
		tokenRejected = false;
		action(token);
	}

	// Resume off the STORE, not one prompt's `onSaved`: `withToken` clears `pendingAction`
	// before it runs the action, so a resumed action cannot be resumed twice.
	$effect(() => {
		if ($writeToken === null) return;
		const resume = pendingAction;
		if (resume === null) return;
		untrack(resume);
	});

	function handleSubmit(values: TicketFormValues): void {
		const body = toTicketCreate(values);
		pendingBody = body;
		withToken((token) => void runPreview(body, token));
	}

	/**
	 * Route a failed write: a REJECTED TOKEN is not a terminal error.
	 *
	 * The caller has already dropped a known-bad token BEFORE the attempt guard (see
	 * `runPreview` / `applyCreate`), because the token is wrong for every write on the
	 * page, not just this one. This only decides what a still-current attempt shows.
	 *
	 * A non-401 failure — the duplicate/invalid id the server enforces (`400
	 * invalid_ticket_id`, `409 write_conflict`) among them — is surfaced on the review
	 * dialog via `ApiErrorView`, from which Cancel returns to the form with the typed
	 * values intact. The dialog is re-asserted open because it is the only place this page
	 * renders a write error, and an apply that fails after a token re-entry has already
	 * torn it down.
	 */
	function handleWriteFailure(apiError: ApiError, retry: (token: string) => void): void {
		if (apiError.code !== WRITE_TOKEN_INVALID_CODE) {
			writeError = apiError;
			previewOpen = true;
			return;
		}
		previewOpen = false;
		writeError = null;
		tokenRejected = true;
		// The token is null now (the caller already cleared it), so this parks `retry` and
		// raises the prompt.
		withToken(retry);
	}

	async function runPreview(body: TicketCreate, token: string): Promise<void> {
		// Open the review dialog up front so the spinner (and any failure) shows where the
		// diff will be, rather than leaving the form looking idle.
		previewOpen = true;
		preview = null;
		writeError = null;
		busy = true;
		const seq = attempt;
		try {
			const result = await previewWrite({ verb: 'create', body }, token);
			if (seq !== attempt) return;
			preview = result;
		} catch (err) {
			const apiError = normalizeError(err);
			// Drop a known-bad token even for an abandoned attempt: it is wrong for every
			// write on the page, so this must not wait on the guard below.
			if (apiError.code === WRITE_TOKEN_INVALID_CODE) clearToken();
			if (seq !== attempt) return;
			handleWriteFailure(apiError, (retryToken) => void runPreview(body, retryToken));
		} finally {
			if (seq === attempt) busy = false;
		}
	}

	function handleConfirm(): void {
		const body = pendingBody;
		// Save is disabled without a preview, but never apply a body nobody reviewed.
		if (body === null) return;
		withToken((token) => void applyCreate(body, token));
	}

	async function applyCreate(body: TicketCreate, token: string): Promise<void> {
		// Re-assert the dialog, symmetric with `runPreview` opening it up front: `withToken`
		// may have closed it while parking (the token went missing at Save), and a resumed
		// retry after a 401 closes it too. Without this the apply runs with the dialog torn
		// down — no diff, no spinner.
		previewOpen = true;
		writeError = null;
		busy = true;
		applying = true;
		const seq = attempt;
		try {
			const result = await createTicket(body, token);
			if (seq !== attempt) return;
			// The new ticket exists on disk now; hand the user straight to it. `ticketId` is
			// the server's authoritative id for what it just wrote.
			await goto(`/tickets/${result.ticketId}`);
		} catch (err) {
			const apiError = normalizeError(err);
			if (apiError.code === WRITE_TOKEN_INVALID_CODE) clearToken();
			if (seq !== attempt) return;
			// Nothing was written, so the dialog stays open on the failure. A rejected token
			// re-raises the prompt and resumes THIS body once a good one is pasted; a
			// duplicate/invalid id (or any other error) is reported on the review dialog.
			handleWriteFailure(apiError, (retryToken) => void applyCreate(body, retryToken));
		} finally {
			busy = false;
			applying = false;
		}
	}

	// Cancelling the review returns to the form with the typed values intact — the form
	// stays mounted underneath precisely so nothing is lost. Refused while the apply is in
	// flight: that write cannot be called off, and dismissing over it would report a
	// cancellation for a create that still lands.
	function handlePreviewCancel(): void {
		if (applying) return;
		// Supersede whatever dry-run is in flight so a cancelled-but-still-settling one's
		// `finally`/`catch` cannot write into `preview`/`writeError`/`previewOpen` for a
		// dialog the user just closed. `busy` is cleared directly because that request's own
		// `finally` will now decline to (its `seq` no longer matches), and nothing else would.
		attempt += 1;
		busy = false;
		previewOpen = false;
		preview = null;
		pendingBody = null;
		writeError = null;
	}
</script>

<div class="space-y-6">
	<header class="flex flex-wrap items-baseline justify-between gap-3">
		<h1 class="text-2xl font-semibold text-text">New ticket</h1>
		<a class="text-sm font-medium text-accent hover:underline" href="/">← back to list</a>
	</header>

	{#if tokenNeeded}
		<!-- Rendered ABOVE the form rather than in place of it: the create that triggered
		     this is still held and resumes on save, so replacing the form would discard the
		     values the user just submitted. -->
		<section class="rounded border border-slate-300 bg-bg p-3">
			<h2 class="mb-2 text-sm font-semibold text-text">Write token required</h2>
			{#if tokenRejected}
				<!-- `alert`: this one IS a failure the user must act on, and it replaces a
				     prompt that would otherwise look identical to the plain "no token yet" case. -->
				<p role="alert" class="mb-2 text-xs text-danger">
					The server rejected the token that was held, so it has been discarded. Paste the current
					one to continue — your ticket is still here.
				</p>
			{/if}
			<WriteTokenPrompt />
		</section>
	{/if}

	<TicketForm mode="create" initial={data.initial} disabled={busy} onSubmit={handleSubmit} />
</div>

<!-- Stacked over the page. `busy={applying}` (not `loading`) keeps Save/Cancel inert
     during the apply; `loading` reflects only the dry-run (`busy && !applying`) so the
     spinner does not paint over the confirmed diff while the write itself runs. -->
<DiffPreviewModal
	open={previewOpen}
	{preview}
	loading={busy && !applying}
	busy={applying}
	error={writeError}
	onConfirm={handleConfirm}
	onCancel={handlePreviewCancel}
/>
