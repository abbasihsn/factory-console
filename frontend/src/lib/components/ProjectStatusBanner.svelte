<script lang="ts">
	import type { RegisteredProjectOut } from '$lib/api';

	// Presentational only: no `$app/*` imports and no fetching, so it renders
	// deterministically under vitest/jsdom from supplied props.
	//
	// A deliberate SIBLING of `SourcesBanner`, not an extension of it.
	// `SourcesBanner` renders exactly ONE case — `/runs`' artifacts being absent
	// for every ticket — and its own header comment says so. This one is about a
	// different subject: the CONSOLE's registry row for the selected project,
	// whose path may have moved, stopped being a factory project, or become
	// unreadable. Widening `SourcesBanner` to carry both would blur two subjects
	// that merely share a shape, so they stay two files.
	//
	// It lives in the shell (`routes/+layout.svelte`) because `/projects` states
	// these conditions per row, but a user who switched project and then navigated
	// is looking at `/runs` or `/graph` — and a degraded project must never render
	// as an ordinary empty one, on any route (ARCHITECTURE.md, "Other factory
	// artefacts (read-only)": an absent source is a named condition, never zero).
	let { project }: { project: RegisteredProjectOut | null } = $props();

	// Derived from the row rather than aliased from a schema: the server inlines
	// T103's `RegistryEntryCondition` into this field instead of publishing it as
	// its own OpenAPI component, so there is no standalone generated alias to
	// import — and deriving it here means a member added server-side widens the
	// union automatically (the same technique `ArtifactSkipReason` uses in
	// `$lib/api/models.ts`). Spelling the union out by hand would put back exactly
	// the guesswork the generated types exist to remove.
	type RegistryEntryCondition = RegisteredProjectOut['condition'];

	type ConditionCopy = {
		title: string;
		body: string;
		// `notice` is for a condition that is degraded but NOT wrong — see
		// `no_factory_dir`. It only picks the border colour; both tones say the
		// same amount.
		tone: 'problem' | 'notice';
	};

	// EXHAUSTIVE by construction: keyed on the generated union, so a condition
	// added server-side fails the type-check here until it has been given a
	// sentence and a remedy of its own, rather than quietly rendering as nothing.
	// `ok` is the one member with no block to render, and is spelled out as `null`
	// rather than omitted so the record still covers the whole union.
	const CONDITION_COPY: Record<RegistryEntryCondition, ConditionCopy | null> = {
		ok: null,
		path_missing: {
			tone: 'problem',
			title: 'This project’s registered path no longer exists.',
			body: 'The directory it was registered under has been moved, renamed or deleted, so every view of this project — tickets, runs, spend, graph — is reading a path that is not there, and is about to be wrong. Re-point the registry entry at the project’s new location, or remove the entry.'
		},
		not_a_project: {
			tone: 'problem',
			title: 'This path is no longer a factory project.',
			body: 'The registered directory exists, but the console does not recognise a factory project in it — its manifest is missing, or is not something the console can read. Nothing here describes the project that was registered. Check that the path still points at the project root, then re-point or remove the registry entry.'
		},
		no_factory_dir: {
			tone: 'notice',
			title: 'This project has no .factory/ directory on this machine.',
			body: 'That is not an error: .factory/ is machine-local and gitignored, so a project whose working copy has never been run here legitimately has none. Run state, run artifacts and spend are therefore unknown on this machine rather than empty; the planning documents and tickets on disk read normally.'
		},
		unreadable: {
			tone: 'problem',
			title: 'This project’s path could not be read.',
			body: 'The console reached the registered path but could not read it at all — a permissions problem or an I/O error, not an empty project. Nothing shown for this project has actually been read from it. Check the directory’s permissions and that its filesystem is mounted, then reload.'
		}
	};

	const condition = $derived(project?.condition ?? null);

	// What to say, or `null` for the two silent cases: no registry entry at all
	// (single-project mode is visually unchanged) and `ok`.
	const copy = $derived.by<ConditionCopy | null>(() => {
		if (condition === null) return null;
		// Indexed through a widened view because the value that ARRIVES may be
		// outside the union a build-time type says it is in — a newer server can
		// report a condition this console has never heard of.
		const known = (CONDITION_COPY as Partial<Record<string, ConditionCopy | null>>)[condition];
		if (known !== undefined) return known;
		// Per the resolution invariant, what could not be understood is RECORDED,
		// never dropped: the fallback names the raw value rather than rendering
		// nothing, which would read as a healthy project.
		return {
			tone: 'problem',
			title: 'This console does not recognise this condition: ' + String(condition),
			body: 'The server reported a project condition this build of the console has no explanation for. It is named here rather than swallowed: something about this project is not ordinary, and this console cannot say what. A newer console should be able to name it.'
		};
	});
</script>

{#if copy}
	<div class="mx-auto max-w-5xl px-4 pt-3">
		<div
			role="status"
			data-testid="project-condition"
			data-condition={condition}
			class="space-y-1 rounded-lg border bg-surface px-4 py-4 {copy.tone === 'problem'
				? 'border-danger'
				: 'border-slate-200'}"
		>
			<p class="font-medium text-text">{copy.title}</p>
			{#if project}
				<p class="text-sm text-muted">
					{project.name} — <code class="font-mono">{project.path}</code>
				</p>
			{/if}
			<p class="text-sm text-muted">{copy.body}</p>
		</div>
	</div>
{/if}
