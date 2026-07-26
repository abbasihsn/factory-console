<script lang="ts">
	import type { ApiError } from '$lib/api/contracts';

	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with a supplied error. `+error.svelte` wires `onReload`.
	//
	// `label` names the button for callers whose action is not a reload — a modal
	// cannot re-run its own fetch, so it dismisses instead and must not show a
	// button reading "Reload". It defaults to `'Reload'`, so the page-level caller
	// keeps today's text.
	let {
		error,
		onReload,
		label = 'Reload'
	}: { error: ApiError; onReload?: () => void; label?: string } = $props();
</script>

<div class="mx-auto max-w-lg px-4 py-16 text-center">
	<p class="font-mono text-sm text-danger">{error.code}</p>
	<h1 class="mt-2 text-xl font-semibold text-text">{error.message}</h1>
	{#if error.hint}
		<p class="mt-2 text-sm text-muted">{error.hint}</p>
	{/if}
	<button
		type="button"
		class="mt-6 rounded bg-accent px-4 py-2 text-sm text-white hover:opacity-90"
		onclick={() => onReload?.()}
	>
		{label}
	</button>
</div>
