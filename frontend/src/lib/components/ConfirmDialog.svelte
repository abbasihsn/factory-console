<script lang="ts">
	import ModalShell from '$lib/components/ModalShell.svelte';

	// Presentational only: no `$app/*` imports and no fetch, so it renders
	// deterministically under vitest/jsdom. The caller owns the action being
	// confirmed — this component only reports the decision through
	// `onConfirm` / `onCancel`. `ModalShell` owns the backdrop, Escape and focus
	// handling; everything below is just this dialog's body.
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
		title: string;
		message: string;
		confirmLabel: string;
		danger?: boolean;
		onConfirm: () => void;
		onCancel: () => void;
	} = $props();
</script>

<ModalShell
	{open}
	{onCancel}
	labelledBy="confirm-dialog-title"
	describedBy="confirm-dialog-message"
	panelClass="w-full max-w-md p-4"
>
	<h2 id="confirm-dialog-title" class="text-base font-semibold text-text">{title}</h2>
	<p id="confirm-dialog-message" class="mt-2 text-sm text-muted">{message}</p>
	<div class="mt-4 flex justify-end gap-2">
		<button
			type="button"
			class="rounded border border-slate-300 px-3 py-1 text-sm text-text hover:bg-bg"
			onclick={onCancel}
		>
			Cancel
		</button>
		<button
			type="button"
			class="rounded px-3 py-1 text-sm text-white hover:opacity-90 {danger
				? 'bg-danger'
				: 'bg-accent'}"
			onclick={onConfirm}
		>
			{confirmLabel}
		</button>
	</div>
</ModalShell>
