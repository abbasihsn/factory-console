<script lang="ts">
	import type { ApiError } from '$lib/api/contracts';

	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with a supplied error.
	//
	// Defaults describe the original call site — this as a whole page. `compact`
	// and `actionLabel` exist for nested call sites (a dialog body), where
	// page-level chrome would be wrong: an `<h1>` would outrank the container's
	// own heading, and the recovery action is rarely a reload. Both default to
	// the page behaviour, so `+error.svelte` is unaffected.
	//
	// Hence `onAction`, not `onReload`: the callback is whatever recovery the call
	// site labelled with `actionLabel` — `invalidateAll` on the error page, but
	// dismissing the message or closing the dialog everywhere else.
	let {
		error,
		onAction,
		compact = false,
		actionLabel = 'Reload'
	}: {
		error: ApiError;
		/** Runs when the button is pressed; whatever `actionLabel` says it does. */
		onAction?: () => void;
		compact?: boolean;
		actionLabel?: string;
	} = $props();
</script>

<div class="mx-auto max-w-lg text-center {compact ? 'px-2 py-6' : 'px-4 py-16'}">
	<p class="font-mono text-sm text-danger">{error.code}</p>
	<!-- Nested in another labelled container, the message is a section heading,
	     not the page title — an `<h1>` there would invert the heading order. -->
	<svelte:element
		this={compact ? 'h2' : 'h1'}
		class="mt-2 font-semibold text-text {compact ? 'text-base' : 'text-xl'}"
	>
		{error.message}
	</svelte:element>
	{#if error.hint}
		<p class="mt-2 text-sm text-muted">{error.hint}</p>
	{/if}
	<button
		type="button"
		class="rounded bg-accent text-sm text-white hover:opacity-90 {compact
			? 'mt-4 px-3 py-1'
			: 'mt-6 px-4 py-2'}"
		onclick={() => onAction?.()}
	>
		{actionLabel}
	</button>
</div>
