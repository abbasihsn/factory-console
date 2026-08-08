<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { get } from 'svelte/store';
	import type { PageData } from './$types';
	import {
		removeProject,
		selectProject,
		type RegisteredProjectOut,
		type RegistryEntryCondition
	} from '$lib/api';
	import { normalizeError, type ApiError } from '$lib/api/contracts';
	import { CONDITION_TITLE } from '$lib/projects/conditionTitle';
	import AddProjectForm from '$lib/components/AddProjectForm.svelte';
	import ApiErrorView from '$lib/components/ApiErrorView.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import WriteTokenPrompt from '$lib/components/WriteTokenPrompt.svelte';
	import { clearToken, WRITE_TOKEN_INVALID_CODE, writeToken } from '$lib/stores/writeToken';

	// The registry management surface: every row the console tracks, what it found
	// at that row's path, and the two writes that change the registry itself.
	//
	// The registry lives on the SERVER, so nothing here is authoritative. There is
	// deliberately NO optimistic UI: the table renders `data.projects` and a write
	// is followed by `invalidateAll()`, which re-runs `+page.ts` and re-reads the
	// list — including the `selected` marker and every row's freshly probed
	// `condition`, neither of which this page could have computed for itself.
	let { data }: { data: PageData } = $props();

	/** Which write a row's button starts. Both are token-gated; only one confirms. */
	type ActionKind = 'select' | 'remove';

	/** A write waiting on a write token: what to do, and to which row. */
	type TokenRequest = { kind: ActionKind; project: RegisteredProjectOut };

	// The row whose write is in flight. An id rather than a boolean because the
	// confirmation has to know WHICH row it is waiting on, but every row's actions go
	// inert for the duration: two writes in flight together would be followed by two
	// `invalidateAll()`s whose order the network decides, and the second row's
	// buttons would be clicked against a table the first one is about to replace.
	let busyId = $state<string | null>(null);
	const busy = $derived(busyId !== null);
	let actionError = $state<ApiError | null>(null);

	// The row awaiting confirmation. `null` closes the dialog — the removal is
	// never started from anywhere else.
	let confirmProject = $state<RegisteredProjectOut | null>(null);

	let tokenRequest = $state<TokenRequest | null>(null);

	// Why the prompt is up. A 401 that silently raises the bare prompt is
	// indistinguishable from never having held a token, so the user re-pastes the
	// SAME rejected value and watches it fail again.
	let tokenRejected = $state(false);

	// Gated on the STORE as well as the parked request, like the ticket routes'
	// prompts: a token pasted into ANOTHER prompt on the page (the header switcher's,
	// say) satisfies this write too, and a panel keyed only on the parked request
	// would go on demanding a token the page already holds.
	const tokenNeeded = $derived(tokenRequest !== null && $writeToken === null);

	// Reconcile the parked request with the store, exactly as the ticket detail route
	// does. The token can arrive from that other prompt, which never calls `start`
	// here; the request would then sit parked forever, and a LATER `clearToken()`
	// (some unrelated 401) would re-raise a prompt for a write the user abandoned
	// long ago — pasting into which would pop a "Remove project?" dialog nobody asked
	// for. Dropping the request, NOT resuming it, is the answer: a write still wanted
	// is one more click on its button.
	$effect(() => {
		if ($writeToken === null) return;
		if (tokenRequest === null) return;
		tokenRequest = null;
		tokenRejected = false;
	});

	const ACTION_CLASS =
		'rounded border border-slate-300 px-3 py-1 text-sm text-text hover:bg-bg disabled:cursor-not-allowed disabled:opacity-60';
	const DANGER_CLASS =
		'rounded border border-red-300 px-3 py-1 text-sm text-danger hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60';

	// The reserved `session` row — the project passed on the command line — is
	// prepended to the list unregistered, and DELETE always answers it with a 409
	// `session_project_not_removable`. Its Remove button is therefore inert rather
	// than a confirmation dialog whose only outcome is that error.
	const UNREGISTERED_REMOVE_TITLE =
		'This is the project passed on the command line — it was never added to the registry, so there is nothing to remove';

	// The selected row is the one `+layout.ts` reads on every route. Removing it
	// nulls the selection server-side, which the root layout load then treats as
	// fatal — so, like a degraded row (see the Select button's own guard below),
	// it stays listed but not removable.
	const SELECTED_REMOVE_TITLE =
		'This is the project currently selected — select a different one before removing it';

	// Full literal Tailwind class strings, never built dynamically, so the JIT
	// scanner keeps them — the rule `RunStateBadge` sets for its own pills.
	const PILL_CLASS = 'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium';
	const OK_PILL = 'bg-green-100 text-green-800';
	const DEGRADED_PILL = 'bg-red-100 text-red-800';
	const UNRECOGNISED_PILL = 'bg-slate-100 text-slate-500';

	// `Record<RegistryEntryCondition, …>`, so a condition added to the backend union
	// and regenerated into the type fails the build here rather than rendering a
	// blank cell — the discipline `RunStateBadge` and `/runs`' `REASON_LABELS` set.
	const CONDITION_CLASSES: Record<RegistryEntryCondition, string> = {
		ok: OK_PILL,
		// Every non-`ok` condition is red: each one means this row cannot serve the
		// console, and telling them apart is the label's job, not the colour's.
		unreadable: DEGRADED_PILL,
		path_missing: DEGRADED_PILL,
		not_a_project: DEGRADED_PILL,
		no_factory_dir: DEGRADED_PILL
	};
	const CONDITION_LABELS: Record<RegistryEntryCondition, string> = {
		ok: 'OK',
		unreadable: 'Unreadable',
		path_missing: 'Path missing',
		not_a_project: 'Not a project',
		no_factory_dir: 'No .factory'
	};
	// Shared with `ProjectStatusBanner` (`$lib/projects/conditionTitle`) — the two
	// surfaces had already begun to word the same condition differently before
	// this module existed, so both now read the one sentence rather than keeping
	// their own copy of it.
	const CONDITION_TITLES: Record<RegistryEntryCondition, string> = {
		ok: 'The path is a factory project this console can read',
		...CONDITION_TITLE
	};

	// The runtime half of the same guarantee. The `Record`s above are what make a NEW
	// condition a compile error, but only once the generated types carry it: until
	// then a value this build has never heard of can still arrive over the wire, and
	// it must render as ITSELF — named, and named as unrecognised — rather than as an
	// empty cell claiming the console knows the row is fine. Hence the one cast: the
	// wire is what is being distrusted here, not the type.
	const UNRECOGNISED_TITLE = 'This console does not recognise this condition';
	function conditionView(condition: string): { label: string; title: string; pill: string } {
		const known = condition as RegistryEntryCondition;
		const label = CONDITION_LABELS[known] as string | undefined;
		if (label === undefined) {
			return { label: condition, title: UNRECOGNISED_TITLE, pill: UNRECOGNISED_PILL };
		}
		return { label, title: CONDITION_TITLES[known], pill: CONDITION_CLASSES[known] };
	}

	/**
	 * Begin a write on `project`, asking for the token first when none is held.
	 *
	 * Ask BEFORE the confirmation, like the ticket detail route: a dialog whose only
	 * possible outcome is a 401 is worse than asking for what the write needs up
	 * front. The removal then still has to be confirmed — the token is the
	 * permission, not the decision.
	 */
	function start(kind: ActionKind, project: RegisteredProjectOut): void {
		if (busyId !== null) return;
		actionError = null;
		if (get(writeToken) === null) {
			tokenRequest = { kind, project };
			return;
		}
		// A token is held again, so whatever the last one was rejected for no longer
		// describes this attempt.
		tokenRequest = null;
		tokenRejected = false;
		if (kind === 'remove') {
			confirmProject = project;
			return;
		}
		void runSelect(project);
	}

	/** Re-enter the parked write once a token has been pasted into the prompt below. */
	function resume(): void {
		const request = tokenRequest;
		if (request === null) return;
		start(request.kind, request.project);
	}

	/**
	 * Run one write for `kind` against `project`, then re-read the page.
	 *
	 * Owns the mechanics shared by both writes — the token check, the busy window,
	 * awaiting the write before invalidating, and routing a failure through `fail`
	 * — so `runSelect`/`runRemove` only supply which call to make. `apply` closes
	 * over the token itself only via its parameter, never reads the store, so this
	 * stays the single place that decides whether one is held.
	 *
	 * The write is AWAITED before anything is invalidated: a load issued against an
	 * uncommitted write would read the OLD state and paint it as the new one.
	 */
	async function runWrite(
		kind: ActionKind,
		project: RegisteredProjectOut,
		apply: (token: string) => Promise<unknown>
	): Promise<void> {
		if (kind === 'remove') {
			// One confirmation is one DELETE. The dialog stays mounted for the whole
			// round-trip, so this guard — not the buttons behind the backdrop — is
			// what stops a double-click sending two.
			if (busyId !== null) return;
		}
		const token = get(writeToken);
		if (token === null) {
			// Dropped between the click (or confirmation) and here — another
			// prompt's 401 can clear it.
			if (kind === 'remove') confirmProject = null;
			tokenRequest = { kind, project };
			return;
		}
		busyId = project.id;
		actionError = null;
		try {
			await apply(token);
			if (kind === 'remove') confirmProject = null;
			// Inside the busy window, not after it: the write is not done when it
			// lands, it is done when the table has re-read the result.
			await invalidateAll();
		} catch (err) {
			if (kind === 'remove') confirmProject = null;
			fail(err, { kind, project });
		} finally {
			busyId = null;
		}
	}

	/**
	 * Point the console at `project`. Unlike the header switcher this stays put —
	 * `/projects` is about the registry itself, and it shows the same rows
	 * whichever one is selected, so there is nothing to navigate away from.
	 */
	async function runSelect(project: RegisteredProjectOut): Promise<void> {
		await runWrite('select', project, (token) => selectProject(project.id, token));
	}

	/**
	 * Forget `project` in the console's registry.
	 *
	 * Nothing on the project's own disk changes — that is what the confirmation says,
	 * and it is the whole difference between this and deleting a ticket.
	 */
	async function runRemove(project: RegisteredProjectOut): Promise<void> {
		await runWrite('remove', project, (token) => removeProject(project.id, token));
	}

	/**
	 * Route a failed write: a rejected token drops the credential and re-raises the
	 * prompt, anything else renders as an error beside the table.
	 *
	 * The token is wrong for EVERY write on the page, not just this one, so it is
	 * discarded rather than retried — otherwise a known-bad value sits in
	 * `sessionStorage` and is resent by each later write.
	 */
	function fail(err: unknown, request: TokenRequest): void {
		const apiError = normalizeError(err);
		if (apiError.code === WRITE_TOKEN_INVALID_CODE) {
			clearToken();
			tokenRequest = request;
			tokenRejected = true;
			return;
		}
		actionError = apiError;
	}
</script>

<div class="space-y-6">
	<h1 class="text-2xl font-semibold text-text">Projects</h1>

	{#if tokenNeeded}
		<section class="rounded border border-slate-300 bg-bg p-3">
			<h2 class="mb-2 text-sm font-semibold text-text">Write token required</h2>
			{#if tokenRejected}
				<!-- `alert`: unlike the bare prompt this IS a failure the user has to act
				     on, and the two are otherwise indistinguishable on screen. -->
				<p role="alert" class="mb-2 text-xs text-danger">
					The server rejected the token that was held, so it has been discarded. Paste the current
					one to change the registry.
				</p>
			{/if}
			<!-- Saving re-enters `start`, which for a removal opens the confirmation:
			     the write still needs confirming, not just a token. -->
			<WriteTokenPrompt onSaved={resume} />
		</section>
	{/if}

	{#if actionError}
		<!-- `compact` because this sits inside the page, not instead of it, and the
		     action dismisses: the registry is still on screen and the write is
		     retryable from the row it came from. -->
		<ApiErrorView
			error={actionError}
			compact
			actionLabel="Dismiss"
			onReload={() => (actionError = null)}
		/>
	{/if}

	<!-- Above the table and outside the empty/non-empty branch below: registering is
	     how an empty registry stops being empty, so the form is what that panel points
	     at. `invalidateAll` re-runs `+page.ts`, which is where the new row — and the
	     switcher's new entry — come from; nothing is patched in here. -->
	<AddProjectForm onAdded={() => void invalidateAll()} />

	{#if data.projects.length === 0}
		<!-- A NAMED state, not a blank table: an empty registry and a registry that
		     could not be read look nothing alike to the loader, and the user needs to
		     be told which one this is. -->
		<p
			data-testid="empty-registry"
			class="rounded-lg border border-slate-200 bg-surface px-4 py-6 text-center text-muted"
		>
			No project is registered yet. Register one above to get started.
		</p>
	{:else}
		<p class="text-sm text-muted">
			Every project this console tracks. Removing one forgets it here — the project's own files are
			never touched.
		</p>
		<table class="w-full overflow-hidden rounded-lg border border-slate-200 bg-surface text-sm">
			<thead class="border-b border-slate-200 text-left text-muted">
				<tr>
					<th class="px-4 py-2 font-medium">Name</th>
					<th class="px-4 py-2 font-medium">Path</th>
					<th class="px-4 py-2 font-medium">Added</th>
					<th class="px-4 py-2 font-medium">Condition</th>
					<th class="px-4 py-2 font-medium">Actions</th>
				</tr>
			</thead>
			<tbody class="divide-y divide-slate-200">
				{#each data.projects as project (project.id)}
					{@const condition = conditionView(project.condition)}
					<!-- `aria-current` marks the row the console is serving, so the selection
					     is not carried by styling alone. -->
					<tr
						data-testid="project-row-{project.id}"
						aria-current={project.selected ? 'true' : undefined}
					>
						<td class="px-4 py-2 text-text">{project.name}</td>
						<!-- `title` carries the full value: a long path truncates in the cell,
						     and the path is what tells two checkouts of one repo apart. -->
						<td class="px-4 py-2 font-mono text-muted" title={project.path}>{project.path}</td>
						<!-- The reserved `session` row was never added to anything, so it has
						     no `addedAt` — an em dash, not a fabricated date. -->
						<td class="px-4 py-2 text-muted">{project.addedAt ?? '—'}</td>
						<td class="px-4 py-2">
							<span class="{PILL_CLASS} {condition.pill}" title={condition.title}>
								{condition.label}
							</span>
						</td>
						<td class="px-4 py-2">
							<div class="flex flex-wrap items-center gap-2">
								<button
									type="button"
									class={ACTION_CLASS}
									disabled={project.selected || busy || project.condition !== 'ok'}
									title={project.condition === 'ok' ? undefined : condition.title}
									onclick={() => start('select', project)}
								>
									{project.selected ? 'Selected' : 'Select'}
								</button>
								<button
									type="button"
									class={DANGER_CLASS}
									disabled={!project.registered || project.selected || busy}
									title={!project.registered
										? UNREGISTERED_REMOVE_TITLE
										: project.selected
											? SELECTED_REMOVE_TITLE
											: undefined}
									onclick={() => start('remove', project)}
								>
									Remove
								</button>
							</div>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}

	{#if confirmProject}
		{@const target = confirmProject}
		<ConfirmDialog
			open={true}
			title="Remove project?"
			message="This forgets {target.name} ({target.path}) in this console's registry. Nothing on disk is touched — the project's own files stay exactly as they are, and it can be added again later."
			confirmLabel="Remove project"
			danger
			busy={busyId === target.id}
			onConfirm={() => void runRemove(target)}
			onCancel={() => (confirmProject = null)}
		/>
	{/if}
</div>
