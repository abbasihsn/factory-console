<script lang="ts">
	import type { ApiError } from '$lib/api/contracts';
	import type { WritePreview } from '$lib/api/models';
	import ApiErrorView from '$lib/components/ApiErrorView.svelte';
	import ModalShell from '$lib/components/ModalShell.svelte';
	import { parseDiffLines, type DiffLineKind } from '$lib/diff/unifiedDiff';

	// Presentational only: no `$app/*` imports and no fetch. The caller runs the
	// dry-run, hands the result in as `preview` (plus `loading` / `error`), and owns
	// the real write it issues from `onConfirm`. Nothing here mutates anything.
	// `ModalShell` owns the backdrop, Escape and focus handling.
	let {
		open,
		preview,
		loading,
		busy = false,
		error,
		onConfirm,
		onCancel
	}: {
		open: boolean;
		preview: WritePreview | null;
		loading: boolean;
		/**
		 * The confirmed WRITE is in flight — narrower than `loading`, which also covers
		 * the dry-run. A dry-run writes nothing and stays cancellable; once the write
		 * is out there is nothing left to call off, so dismissal must be refused
		 * rather than imply it stopped something.
		 */
		busy?: boolean;
		error: ApiError | null;
		onConfirm: () => void;
		onCancel: () => void;
	} = $props();

	// Every route out — Cancel, Escape, the backdrop — goes through this guard, the
	// same shape `ConfirmDialog` uses. Without it the user can dismiss the dialog
	// mid-write and still have the write land, told they cancelled a write that applied.
	function handleCancel(): void {
		if (busy) return;
		onCancel();
	}

	// Confirm is guarded here as well as by `canConfirm`, so "one confirmation is one
	// write" is this component's own guarantee rather than something it inherits from
	// a caller that happens to also raise `loading` for the apply.
	function handleConfirm(): void {
		if (busy) return;
		onConfirm();
	}

	// A preview covers every file the write touches, so the body is a list of
	// per-file diffs — `files` is optional in the contract, hence the fallback.
	const files = $derived(preview?.diff.files ?? []);

	// Saving is only meaningful once a preview has actually arrived: nothing to
	// confirm while the dry-run is in flight, failed, or was never made.
	const canConfirm = $derived(!loading && !busy && error === null && preview !== null);

	const LINE_CLASSES: Record<DiffLineKind, string> = {
		add: 'bg-emerald-50 text-emerald-700',
		del: 'bg-red-50 text-danger',
		hunk: 'text-accent',
		meta: 'text-muted',
		context: 'text-text'
	};
</script>

<ModalShell
	{open}
	onCancel={handleCancel}
	labelledBy="diff-preview-title"
	describedBy="diff-preview-description"
	panelClass="flex max-h-[85vh] w-full max-w-3xl flex-col"
>
	<div class="border-b border-slate-300 px-4 py-3">
		<h2 id="diff-preview-title" class="text-base font-semibold text-text">Review changes</h2>
		<!-- Carries an id and is wired as the dialog's description: this sentence
		     is the reassurance that nothing has been written yet, so a screen
		     reader must announce it with the dialog, not only sighted users. -->
		<p id="diff-preview-description" class="mt-1 text-xs text-muted">
			Nothing is written until you save. This is the exact diff the write would produce.
		</p>
	</div>

	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<div
		class="flex-1 overflow-y-auto px-4 py-3"
		tabindex="0"
		role="region"
		aria-labelledby="diff-preview-title"
	>
		{#if loading}
			<div class="flex items-center gap-2 py-6 text-sm text-muted" role="status">
				<span
					class="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-accent"
					aria-hidden="true"
				></span>
				Loading preview…
			</div>
		{:else if error}
			<!-- `compact` keeps the page-level chrome out of a dialog body, and the
			     action is labelled for what it does: this call site has no retry
			     hook, so it closes the dialog and the caller re-runs the dry-run
			     when the action is retried. -->
			<ApiErrorView {error} compact actionLabel="Close" onReload={handleCancel} />
		{:else if preview === null}
			<p class="py-6 text-sm text-muted">No preview to review yet.</p>
		{:else}
			<!-- Keyed by position, not `path`: nothing upstream promises the server
			     never repeats a path, and a duplicate key would take the page down. -->
			{#each files as file, fileIndex (fileIndex)}
				<section class="mb-3 rounded border border-slate-300">
					<header
						class="flex items-baseline justify-between gap-2 border-b border-slate-300 bg-bg px-2 py-1"
					>
						<span class="font-mono text-xs text-text">{file.path}</span>
						<span class="text-xs uppercase text-muted">{file.changeKind}</span>
					</header>
					<pre
						class="overflow-x-auto px-2 py-1 font-mono text-xs leading-5">{#each parseDiffLines(file.diff) as line, index (index)}<span
								class="block min-h-[1.25em] {LINE_CLASSES[line.kind]}">{line.text}</span
							>{/each}</pre>
				</section>
			{:else}
				<p class="py-6 text-sm text-muted">No file changes in this preview.</p>
			{/each}
		{/if}
	</div>

	<div class="flex justify-end gap-2 border-t border-slate-300 px-4 py-3">
		<button
			type="button"
			class="rounded border border-slate-300 px-3 py-1 text-sm text-text hover:bg-bg disabled:cursor-not-allowed disabled:opacity-60"
			disabled={busy}
			onclick={handleCancel}
		>
			Cancel
		</button>
		<button
			type="button"
			class="rounded bg-accent px-3 py-1 text-sm text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
			disabled={!canConfirm}
			onclick={handleConfirm}
		>
			Save
		</button>
	</div>
</ModalShell>
