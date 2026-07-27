<script lang="ts">
	import type { Snippet } from 'svelte';

	// The dialog CHROME both of this lane's modals share: backdrop, centered panel,
	// `role="dialog"` + accessible name, Escape-to-cancel, and the Cancel button.
	// Extracted so DiffPreviewModal and ConfirmDialog do not each carry a copy of it
	// (and cannot drift apart on accessibility).
	//
	// Presentational only: no `$app/*` imports, no fetch, no writes — it renders
	// deterministically under vitest/jsdom from supplied props, and every dismissal
	// path calls the caller's `onCancel`.
	let {
		open,
		title,
		body,
		actions,
		onCancel
	}: {
		open: boolean;
		/** Visible heading, and the dialog's accessible name via `aria-labelledby`. */
		title: string;
		/** The panel's content between the heading and the buttons. */
		body: Snippet;
		/** Confirming action(s), rendered after the built-in Cancel button. */
		actions: Snippet;
		/** Called by the Cancel button, the Escape key, and a backdrop click. */
		onCancel: () => void;
	} = $props();

	// Unique per instance, so two mounted dialogs cannot both claim the same
	// `aria-labelledby` target.
	const titleId = $props.id();

	let cancelButton: HTMLButtonElement | undefined = $state();

	// Opening moves focus into the dialog, landing it on the LEAST destructive
	// action — these dialogs gate writes and deletes, so a stray Enter should
	// dismiss, never apply. Focus is not trapped; Escape and Cancel are the exits.
	$effect(() => {
		if (open) cancelButton?.focus();
	});

	// `<svelte:window>` must stay at the top level of the component, so the listener
	// outlives the `{#if}` and checks `open` itself.
	function handleKeydown(event: KeyboardEvent): void {
		if (!open || event.key !== 'Escape') return;
		event.preventDefault();
		onCancel();
	}
</script>

<svelte:window onkeydown={handleKeydown} />

{#if open}
	<div class="fixed inset-0 z-40 flex items-center justify-center p-4">
		<!-- Mouse-only dismissal affordance. `aria-hidden` + `tabindex="-1"` keep it out
		     of the tab order and the accessibility tree, since Escape and the Cancel
		     button already cover keyboard and assistive-tech users. -->
		<button
			type="button"
			tabindex="-1"
			aria-hidden="true"
			data-testid="modal-backdrop"
			class="absolute inset-0 cursor-default bg-slate-900/40"
			onclick={onCancel}
		></button>
		<div
			class="relative flex max-h-[85vh] w-full max-w-3xl flex-col gap-4 rounded border border-slate-300 bg-surface p-4 shadow-lg"
			role="dialog"
			aria-modal="true"
			aria-labelledby={titleId}
		>
			<h2 id={titleId} class="text-base font-semibold text-text">{title}</h2>
			<div class="min-h-0 flex-1 overflow-auto">{@render body()}</div>
			<div class="flex justify-end gap-2">
				<button
					bind:this={cancelButton}
					type="button"
					class="rounded border border-slate-300 px-3 py-1 text-sm text-text hover:bg-bg"
					onclick={onCancel}
				>
					Cancel
				</button>
				{@render actions()}
			</div>
		</div>
	</div>
{/if}
