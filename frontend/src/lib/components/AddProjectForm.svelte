<script lang="ts">
	import { untrack } from 'svelte';
	import { get } from 'svelte/store';
	import { addProject } from '$lib/api';
	import type { AddProjectRequest } from '$lib/api/models';
	import { normalizeError, type ApiError } from '$lib/api/contracts';
	import ApiErrorView from '$lib/components/ApiErrorView.svelte';
	import WriteTokenPrompt from '$lib/components/WriteTokenPrompt.svelte';
	import { clearToken, WRITE_TOKEN_INVALID_CODE, writeToken } from '$lib/stores/writeToken';

	// The one way the registry ever gets populated: a path, an optional label, and
	// the server's verdict on both.
	//
	// The DISCIPLINE here is that the console validates exactly one thing — that the
	// path box is not empty — and the server validates everything else. Whether the
	// path exists, is a directory, carries a `docs/planning/tickets.json`, or is
	// already registered are four distinct refusals with four distinct codes, and
	// only the machine holding the filesystem can tell them apart. A second copy of
	// those rules in the browser would be a guess that drifts, so failures are
	// rendered THROUGH `ApiErrorView`, which prints the envelope's `code`, `message`
	// and `hint` verbatim.
	//
	// Nothing here is authoritative about the registry either: a successful add
	// re-reads via the host's `onAdded` rather than patching a row in optimistically.
	let { onAdded }: { onAdded?: () => void } = $props();

	// Per-INSTANCE ids, for the reason `WriteTokenPrompt` gives for its own: a
	// hardcoded id repeated in the document would leave a label naming somebody
	// else's input.
	const uid = $props.id();
	const pathId = `${uid}-path`;
	const nameId = `${uid}-name`;

	let path = $state('');
	let name = $state('');

	// Covers the whole round trip, including a retry resumed after a token
	// rejection. `POST /projects` probes the filesystem before it answers, which on a
	// cold or networked mount is slow enough to invite a second click — and the add
	// is NOT idempotent (a repeat is a `409 duplicate_project_path`), so a
	// double-submit would report a failure the user's first click caused.
	let busy = $state(false);
	let addError = $state<ApiError | null>(null);

	// The only client-side rule: an empty path box is not a path. Everything the
	// server could disagree with is left to the server.
	const canSubmit = $derived(path.trim().length > 0);

	// What to do once a token exists. Holding the ACTION (not a token we don't have)
	// is what lets one detour serve both a first submit and a retry — the same shape
	// `/tickets/new` uses.
	let pendingAction = $state<(() => void) | null>(null);

	// Gated on the STORE as well as the parked action, like the other prompts on
	// this page: a token pasted into another one satisfies this write too, and a
	// panel keyed only on the parked action would go on demanding a token the page
	// already holds.
	const tokenNeeded = $derived(pendingAction !== null && $writeToken === null);

	// Why the prompt is up. A 401 that silently raises the bare prompt is
	// indistinguishable from never having held a token, so the user re-pastes the
	// SAME rejected value and watches it fail again.
	let tokenRejected = $state(false);

	// Full literal Tailwind class strings, matching `/projects`' own controls so the
	// form reads as part of that page — its own copies, though: the page's consts are
	// private to it, and importing across that seam is not a thing Svelte offers.
	const INPUT_CLASS =
		'rounded border border-slate-300 bg-surface px-2 py-1 text-sm text-text disabled:cursor-not-allowed disabled:opacity-60';
	const ACTION_CLASS =
		'rounded border border-slate-300 px-3 py-1 text-sm text-text hover:bg-bg disabled:cursor-not-allowed disabled:opacity-60';

	/**
	 * Run `action` with this session's write token, or park it behind the prompt.
	 *
	 * The token is read imperatively with `get` rather than through a `$writeToken`
	 * subscription: what matters is the value held when the request goes out, and
	 * reading it here also lets the resume effect below pick up a token the prompt
	 * has just stored with no ordering subtlety.
	 *
	 * `tokenRejected` is cleared only once a token is actually HELD — not on every
	 * submit — so parking behind a still-missing token cannot erase the explanation
	 * the prompt is currently showing.
	 */
	function withToken(action: (token: string) => void): void {
		const token = get(writeToken);
		if (token === null) {
			pendingAction = () => withToken(action);
			return;
		}
		pendingAction = null;
		tokenRejected = false;
		action(token);
	}

	// Resume off the STORE, not this prompt's `onSaved`: the token can arrive from
	// any other prompt on the page. `withToken` clears `pendingAction` before running
	// the action, so a resumed action cannot be resumed twice.
	$effect(() => {
		if ($writeToken === null) return;
		const resume = pendingAction;
		if (resume === null) return;
		untrack(resume);
	});

	/** The body the fields currently describe. Trimmed, and `name` omitted when blank. */
	function currentBody(): AddProjectRequest {
		const label = name.trim();
		// Omitted rather than sent empty: the server labels the row with the
		// directory's final component when there is no name, and `''` is not that.
		return label.length === 0 ? { path: path.trim() } : { path: path.trim(), name: label };
	}

	function handleSubmit(event: SubmitEvent): void {
		event.preventDefault();
		// The button is disabled in both cases, but a stray Enter must not slip past.
		if (!canSubmit || busy) return;
		start();
	}

	function start(): void {
		addError = null;
		const body = currentBody();
		withToken((token) => void run(body, token));
	}

	/**
	 * POST the body, then let the host re-read.
	 *
	 * The fields are cleared only after the server has ACCEPTED the path — a refusal
	 * leaves them exactly as typed, which is what makes "Try again" (or a one-word
	 * correction) possible at all.
	 */
	async function run(body: AddProjectRequest, token: string): Promise<void> {
		busy = true;
		addError = null;
		try {
			await addProject(body, token);
			path = '';
			name = '';
			onAdded?.();
		} catch (err) {
			const apiError = normalizeError(err);
			if (apiError.code === WRITE_TOKEN_INVALID_CODE) {
				// The token is wrong for every write on the page, not just this one, so
				// it is dropped rather than retried — otherwise a known-bad value sits in
				// `sessionStorage` and is resent by each later write. THIS body is parked
				// and resumes verbatim once a good token is pasted.
				clearToken();
				tokenRejected = true;
				withToken((retryToken) => void run(body, retryToken));
				return;
			}
			addError = apiError;
		} finally {
			busy = false;
		}
	}

	/** "Try again" on a named refusal: re-submit whatever the fields say NOW. */
	function retry(): void {
		if (!canSubmit || busy) return;
		start();
	}
</script>

<section class="space-y-3 rounded-lg border border-slate-200 bg-surface p-4">
	<h2 class="text-sm font-semibold text-text">Register a project</h2>
	<p class="text-sm text-muted">
		The path of a factory project on this server's disk. Whether it can be tracked is decided there
		— this console does not inspect the filesystem.
	</p>

	<form class="flex flex-wrap items-end gap-3" onsubmit={handleSubmit}>
		<div class="flex min-w-64 grow flex-col gap-1">
			<label class="text-xs text-muted" for={pathId}>Project path</label>
			<!-- Monospace: a path is read character by character — a stray space or a
			     confusable glyph is exactly what a refusal will be about. -->
			<input
				id={pathId}
				type="text"
				class="{INPUT_CLASS} font-mono"
				autocomplete="off"
				spellcheck="false"
				placeholder="/home/dev/my-project"
				required
				disabled={busy}
				bind:value={path}
			/>
		</div>
		<div class="flex min-w-48 flex-col gap-1">
			<label class="text-xs text-muted" for={nameId}>Name (optional)</label>
			<input
				id={nameId}
				type="text"
				class={INPUT_CLASS}
				autocomplete="off"
				placeholder="Defaults to the directory name"
				disabled={busy}
				bind:value={name}
			/>
		</div>
		<button type="submit" class={ACTION_CLASS} disabled={!canSubmit || busy}>
			{busy ? 'Registering…' : 'Register'}
		</button>
	</form>

	{#if tokenNeeded}
		<!-- Below the form rather than instead of it: the submit is still parked and
		     resumes on save, so replacing the form would discard the path just typed. -->
		<div class="rounded border border-slate-300 bg-bg p-3">
			<h3 class="mb-2 text-sm font-semibold text-text">Write token required</h3>
			{#if tokenRejected}
				<!-- `alert`: unlike the bare prompt this IS a failure the user has to act
				     on, and the two are otherwise indistinguishable on screen. -->
				<p role="alert" class="mb-2 text-xs text-danger">
					The server rejected the token that was held, so it has been discarded. Paste the current
					one to finish registering this project.
				</p>
			{/if}
			<!-- No `onSaved`: resumption is store-driven (the effect above), so a token
			     pasted into any prompt on the page continues this add. -->
			<WriteTokenPrompt />
		</div>
	{:else if addError}
		<!-- `compact` because this sits inside the page, not instead of it. The view
		     prints the server's `code`, `message` and `hint` VERBATIM: which of the
		     several ways a path can be wrong fired is the whole point of this form's
		     failure state, and a message the console invented would erase it. -->
		<ApiErrorView error={addError} compact actionLabel="Try again" onReload={retry} />
	{/if}
</section>
