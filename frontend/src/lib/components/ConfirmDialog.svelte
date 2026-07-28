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
		busy = false,
		onConfirm,
		onCancel
	}: {
		open: boolean;
		title: string;
		message: string;
		confirmLabel: string;
		danger?: boolean;
		/**
		 * The confirmed action is in flight. This dialog stays mounted for the whole
		 * round-trip, so without it a second click on confirm would run the action
		 * again — one confirmation must never become two writes.
		 */
		busy?: boolean;
		onConfirm: () => void;
		onCancel: () => void;
	} = $props();

	// Both decisions are guarded in the handler, not only by `disabled` on the
	// button: the attribute is what a mouse sees, while these are what actually
	// hold — and Escape and the backdrop never had an attribute to check.
	function handleConfirm(): void {
		if (busy) return;
		onConfirm();
	}

	// Dismissal cannot recall a write already in flight, so while `busy` this dialog
	// must not LOOK dismissable. Every route out — Cancel, Escape, the backdrop —
	// goes through this guard: closing over a running action would tell the user
	// they stopped something that is still going to happen.
	function handleCancel(): void {
		if (busy) return;
		onCancel();
	}
</script>

<ModalShell
	{open}
	onCancel={handleCancel}
	labelledBy="confirm-dialog-title"
	describedBy="confirm-dialog-message"
	panelClass="w-full max-w-md p-4"
>
	<h2 id="confirm-dialog-title" class="text-base font-semibold text-text">{title}</h2>
	<p id="confirm-dialog-message" class="mt-2 text-sm text-muted">{message}</p>
	<div class="mt-4 flex justify-end gap-2">
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
			class="rounded px-3 py-1 text-sm text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60 {danger
				? 'bg-danger'
				: 'bg-accent'}"
			disabled={busy}
			onclick={handleConfirm}
		>
			{confirmLabel}
		</button>
	</div>
</ModalShell>
