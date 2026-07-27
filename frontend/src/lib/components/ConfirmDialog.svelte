<script lang="ts">
	import ModalShell from '$lib/components/ModalShell.svelte';

	// Generic "are you sure?" gate for a mutating action — delete is the first
	// caller. Presentational only: no `$app/*`, no fetch, no write from here; the
	// caller performs the action in `onConfirm`.
	let {
		open,
		title,
		message,
		confirmLabel,
		danger = false,
		onConfirm,
		onCancel
	}: {
		open: boolean;
		/** Heading, and the dialog's accessible name. */
		title: string;
		/** What confirming will do, in one sentence. */
		message: string;
		/** Label of the confirming button, e.g. `'Delete ticket'`. */
		confirmLabel: string;
		/** Styles the confirming button as destructive. */
		danger?: boolean;
		onConfirm: () => void;
		onCancel: () => void;
	} = $props();

	// Full literal class strings so Tailwind's JIT scanner keeps them.
	const BUTTON_BASE = 'rounded px-3 py-1 text-sm text-white hover:opacity-90';
	const confirmClass = $derived(danger ? `${BUTTON_BASE} bg-danger` : `${BUTTON_BASE} bg-accent`);
</script>

<ModalShell {open} {title} {onCancel} body={messageBody} actions={confirmAction} />

{#snippet messageBody()}
	<p class="text-sm text-text">{message}</p>
{/snippet}

{#snippet confirmAction()}
	<button type="button" class={confirmClass} onclick={onConfirm}>{confirmLabel}</button>
{/snippet}
