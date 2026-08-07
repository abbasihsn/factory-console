<script lang="ts">
	import { untrack } from 'svelte';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { get } from 'svelte/store';
	import { selectProject, type RegisteredProjectOut } from '$lib/api';
	import { normalizeError, type ApiError } from '$lib/api/contracts';
	import ApiErrorView from '$lib/components/ApiErrorView.svelte';
	import WriteTokenPrompt from '$lib/components/WriteTokenPrompt.svelte';
	import { switchTarget } from '$lib/projects/switchTarget';
	import { clearToken, WRITE_TOKEN_INVALID_CODE, writeToken } from '$lib/stores/writeToken';

	// The header's project switcher. Like `NavSearch` — and unlike the prop-only
	// `TopBar` that hosts both — this component OWNS its action: the write, the
	// token detour and the navigation all live here, so `TopBar` stays
	// presentational and `$app`-free.
	//
	// The selection lives on the SERVER, so nothing here is authoritative: the
	// props are what the last layout load read, and the only way this component
	// changes them is by writing the selection and re-running the loads.
	let {
		projects,
		selectedId
	}: { projects: readonly RegisteredProjectOut[]; selectedId: string | null } = $props();

	// One switch at a time. A second change while the PUT is in flight would race
	// it — two writes whose order is decided by the network, with the loads that
	// follow settling in either order on top — so the control goes inert (and says
	// so, via `aria-busy`) until the first one has landed and re-loaded.
	let busy = $state(false);

	// The id the user chose that has not landed yet: it drives the retry, both for
	// the token detour (resume once a token is pasted) and for a plain failure
	// ("Try again"). Cleared only by a switch that succeeded — or by the user
	// selecting the current project again, which abandons the attempt.
	let pendingId = $state<string | null>(null);
	let switchError = $state<ApiError | null>(null);

	// Gated on the STORE as well as the parked id, like the ticket routes' prompts:
	// a token pasted into one of THOSE satisfies this switch too, so a panel keyed
	// only on the parked id would go on demanding a token it already has.
	const tokenNeeded = $derived(pendingId !== null && $writeToken === null);

	// True only while this switch is genuinely waiting on a token — parked (never
	// had one) or rejected (had one revoked) — never while it is in flight with one
	// already held, or has failed for an unrelated reason. That distinction is what
	// lets the effect below resume exactly the parked/rejected case without ALSO
	// firing (or re-firing in a loop) whenever `busy` or `$writeToken` merely change
	// for some other reason.
	let parked = $state(false);

	// Resumption is driven off the STORE, not off `WriteTokenPrompt`'s `onSaved`,
	// for the same reason as the ticket routes' prompts (`+page.svelte`,
	// `EditTicketModal.svelte`): this switcher renders in `TopBar` on every route,
	// so a token pasted into a DIFFERENT prompt on the same page (e.g. the ticket
	// detail route's own delete/edit prompt) must resume a switch parked here too.
	$effect(() => {
		if (!parked || busy || $writeToken === null) return;
		const id = pendingId;
		if (id === null) return;
		parked = false;
		untrack(() => switchTo(id));
	});

	// Why the prompt is up. A 401 that silently raises the bare prompt is
	// indistinguishable from never having held a token, so the user re-pastes the
	// SAME rejected value and watches it fail again.
	let tokenRejected = $state(false);

	// What the control shows: the attempt in flight (or the one that failed, which
	// is the row the error beside it is about), falling back to what the server
	// last said is selected.
	const shownId = $derived(pendingId ?? selectedId ?? '');

	// The last entry of the dropdown is a NAVIGATION, not a project, so it needs a
	// value no minted project id can collide with — the id is the only thing a
	// `<select>` change hands back, and the two must never be confused.
	const MANAGE_VALUE = '__manage__';

	function handleChange(event: Event): void {
		const select = event.currentTarget as HTMLSelectElement;
		const id = select.value;
		if (id === MANAGE_VALUE) {
			// Restore the control before leaving: this switcher lives in `TopBar` and
			// survives the navigation, so a `<select>` left showing "Manage projects…"
			// would sit over every route claiming that is the current project.
			select.value = shownId;
			void goto('/projects');
			return;
		}
		// Re-selecting the current project is a no-op: the write would be idempotent
		// anyway, but there is no reason to spend a round-trip and a full reload on
		// it — and it is also how the user backs out of a failed attempt.
		if (id === selectedId) {
			pendingId = null;
			switchError = null;
			tokenRejected = false;
			parked = false;
			return;
		}
		void switchTo(id);
	}

	/**
	 * Write the selection, then re-load whatever route we are on.
	 *
	 * The write is AWAITED before anything is invalidated: a load issued against an
	 * uncommitted selection would read the OLD project and paint it as the new one.
	 * With the write settled first, the reads that follow can only see the new
	 * selection — and SvelteKit discards superseded loads, so an in-flight read for
	 * the old project cannot land on top either.
	 */
	async function switchTo(id: string): Promise<void> {
		pendingId = id;
		const token = get(writeToken);
		if (token === null) {
			// Parked: `tokenNeeded` raises the prompt; the store-watching effect above
			// resumes once a token lands, from this prompt or any other on the page.
			parked = true;
			return;
		}
		parked = false;
		busy = true;
		switchError = null;
		// A token is held again, so whatever the last one was rejected for no longer
		// describes this attempt. (Invisible either way while a token exists — the
		// prompt it explains is gated on `tokenNeeded` — but it must not survive to
		// mislabel a LATER detour.)
		tokenRejected = false;
		try {
			await selectProject(id, token);
			// The reload is INSIDE the busy window, not after it: the switch is not
			// done when the write lands, it is done when the route has re-read the new
			// project, and re-enabling the control before then invites a second switch
			// over a shell still showing the previous one.
			const target = switchTarget(page.url.pathname);
			if (target !== null) {
				await goto(target, { invalidateAll: true });
			} else {
				await invalidateAll();
			}
			// Held until the reload has landed, so the control does not flick back to
			// the outgoing project for the width of the navigation — by here the props
			// carry the new selection and the two agree.
			pendingId = null;
		} catch (err) {
			const apiError = normalizeError(err);
			if (apiError.code === WRITE_TOKEN_INVALID_CODE) {
				// The token is wrong for every write on the page, not just this one, so
				// drop it and ask for a replacement rather than reporting a failure the
				// user cannot act on.
				clearToken();
				tokenRejected = true;
				parked = true;
			} else {
				switchError = apiError;
			}
		} finally {
			busy = false;
		}
	}

	function retry(): void {
		const id = pendingId;
		if (id === null) return;
		void switchTo(id);
	}
</script>

<!-- Nothing to switch between: with fewer than two rows (which is also what a
     registry that could not be read degrades to) the dropdown would offer the
     user their own current project, so single-project mode looks exactly as it
     did before this existed. -->
{#if projects.length >= 2}
	<div class="flex shrink-0 flex-col gap-1">
		<select
			aria-label="Project"
			aria-busy={busy}
			disabled={busy}
			class="max-w-48 rounded border border-slate-300 bg-surface px-2 py-1 text-sm text-text disabled:cursor-not-allowed disabled:opacity-60"
			value={shownId}
			onchange={handleChange}
		>
			{#each projects as project (project.id)}
				<!-- The path is the disambiguator: two registered checkouts of the same
				     repository carry the same name, and only the directory says which
				     one this row is. A degraded row (`condition !== 'ok'`) stays LISTED
				     — `listProjects`'s contract never drops one — but disabled: the
				     server accepts a switch onto it, and `+layout.ts`'s project read then
				     treats the resulting 409 as fatal, replacing the whole shell
				     (switcher included) with no way back to a working project. -->
				<option value={project.id} title={project.path} disabled={project.condition !== 'ok'}>
					{project.name}{project.condition === 'ok' ? '' : ' (unavailable)'}
				</option>
			{/each}
			<!-- Trailing, after every project: the registry management route, where a
			     row can be added or removed rather than merely switched to. -->
			<option value={MANAGE_VALUE}>Manage projects…</option>
		</select>

		{#if tokenNeeded}
			<!-- Inline, under the control: the switch is still held in `pendingId` and
			     resumes on save, so this asks for what the write needs rather than
			     reporting that it failed. -->
			<div class="rounded border border-slate-300 bg-bg p-2">
				{#if tokenRejected}
					<!-- `alert`: this one IS a failure the user has to act on, and it
					     replaces a prompt that would otherwise look identical to the
					     plain "no token yet" case. -->
					<p role="alert" class="mb-1 text-xs text-danger">
						The server rejected the token that was held, so it has been discarded. Paste the current
						one to finish switching project.
					</p>
				{/if}
				<!-- No `onSaved`: resumption is store-driven (the effect above), not tied
				     to THIS prompt instance saving — see its comment. -->
				<WriteTokenPrompt />
			</div>
		{:else if switchError}
			<!-- `compact`: this renders inside the header, where the page-level `<h1>`
			     and "Reload" of the boundary view would both be wrong. -->
			<ApiErrorView error={switchError} compact actionLabel="Try again" onReload={retry} />
		{/if}
	</div>
{/if}
