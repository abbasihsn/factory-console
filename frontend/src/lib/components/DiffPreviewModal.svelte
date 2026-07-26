<script lang="ts">
	import type { FileDiff, WritePreview } from '$lib/api';
	import type { ApiError } from '$lib/api/contracts';
	import ApiErrorView from '$lib/components/ApiErrorView.svelte';
	import ModalShell from '$lib/components/ModalShell.svelte';
	import { parseDiffLines, type DiffLineKind } from '$lib/diff/unifiedDiff';

	// The review gate every write passes through: the caller runs the dry-run,
	// hands the resulting `WritePreview` in, and only its `onConfirm` issues the
	// real write. Presentational only — no `$app/*`, no fetch, no write from here.
	let {
		open,
		preview,
		loading,
		error,
		onConfirm,
		onCancel
	}: {
		open: boolean;
		/** Dry-run result to review; `null` before one has been fetched. */
		preview: WritePreview | null;
		loading: boolean;
		/** Failure of the dry-run itself; rendered through `ApiErrorView`. */
		error: ApiError | null;
		onConfirm: () => void;
		onCancel: () => void;
	} = $props();

	// `WritePreview` carries its diffs NESTED — `diff.files` — and `files` is
	// OPTIONAL, so an absent list is normalized to `[]` here and every branch below
	// reads this one derived value.
	const files = $derived<readonly FileDiff[]>(preview?.diff.files ?? []);
	const hasChanges = $derived(files.length > 0);

	// Classify each file's diff text ONCE per preview rather than on every render.
	const classifiedFiles = $derived(
		files.map((file) => ({ ...file, lines: parseDiffLines(file.diff) }))
	);

	// Save is gated on there being something reviewed and safe to apply: not
	// mid-fetch, no failed dry-run, and at least one file the write would touch.
	const confirmDisabled = $derived(loading || error !== null || !hasChanges);

	// Nothing-to-show covers two different truths, so say which one it is rather
	// than leaving the panel blank.
	const emptyMessage = $derived(
		preview ? 'This write would not change any files.' : 'No preview to review yet.'
	);

	// Full literal class strings so Tailwind's JIT scanner keeps them.
	const LINE_CLASSES: Record<DiffLineKind, string> = {
		add: 'text-emerald-700',
		del: 'text-danger',
		hunk: 'text-accent',
		meta: 'text-muted',
		context: 'text-text'
	};

	const CHANGE_KIND_CLASSES: Record<FileDiff['changeKind'], string> = {
		create: 'text-emerald-700',
		modify: 'text-accent',
		delete: 'text-danger'
	};

	function handleConfirm(): void {
		// The button is disabled in these states, but a stray Enter must not slip a
		// write through the gate.
		if (confirmDisabled) return;
		onConfirm();
	}
</script>

<ModalShell {open} title="Review changes" {onCancel} body={diffBody} actions={saveAction} />

{#snippet diffBody()}
	{#if loading}
		<div class="flex items-center gap-2 text-sm text-muted" role="status">
			<span
				class="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-accent"
				aria-hidden="true"
			></span>
			Loading preview…
		</div>
	{:else if error}
		<!-- The dialog owns no fetch, so it cannot re-run the failed dry-run itself.
		     Point ApiErrorView's button at dismissal — the caller re-runs the preview
		     on the next attempt — rather than leaving an inert button in the panel. -->
		<ApiErrorView {error} onReload={onCancel} />
	{:else if !hasChanges}
		<p class="text-sm text-muted">{emptyMessage}</p>
	{:else}
		<div class="flex flex-col gap-3">
			{#each classifiedFiles as file (file.path)}
				<section class="rounded border border-slate-200">
					<h3 class="flex items-baseline gap-2 border-b border-slate-200 px-2 py-1 text-xs">
						<span class="font-mono text-text">{file.path}</span>
						<span class="uppercase {CHANGE_KIND_CLASSES[file.changeKind]}">{file.changeKind}</span>
					</h3>
					<!-- prettier-ignore -->
					<pre class="overflow-x-auto px-2 py-1 font-mono text-xs leading-5">{#each file.lines as line}<span class="block min-h-5 {LINE_CLASSES[line.kind]}">{line.text}</span>{/each}</pre>
				</section>
			{/each}
		</div>
	{/if}
{/snippet}

{#snippet saveAction()}
	<button
		type="button"
		class="rounded bg-accent px-3 py-1 text-sm text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
		disabled={confirmDisabled}
		onclick={handleConfirm}
	>
		Save
	</button>
{/snippet}
