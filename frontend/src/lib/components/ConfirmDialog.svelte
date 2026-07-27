<script lang="ts">
	// Presentational only: no `$app/*` imports and no fetch, so it renders
	// deterministically under vitest/jsdom. The caller owns the action being
	// confirmed — this component only reports the decision through
	// `onConfirm` / `onCancel`.
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

	let panel = $state<HTMLElement | null>(null);

	// Move focus into the dialog when it opens so the Escape handler and the
	// buttons are reachable without a mouse.
	$effect(() => {
		if (open) panel?.focus();
	});

	// `<svelte:window>` may only sit at the top level, so the listener is always
	// attached and the closed case is rejected here instead.
	function handleKeydown(event: KeyboardEvent): void {
		if (!open || event.key !== 'Escape') return;
		event.preventDefault();
		onCancel();
	}
</script>

<svelte:window onkeydown={handleKeydown} />

{#if open}
	<div class="fixed inset-0 z-50 flex items-center justify-center p-4">
		<!-- A real button, not a click-handling div: the backdrop stays keyboard
		     reachable and needs no a11y escape hatch. -->
		<button
			type="button"
			class="absolute inset-0 bg-slate-900/40"
			aria-label="Dismiss dialog"
			onclick={onCancel}
		></button>
		<div
			bind:this={panel}
			class="relative w-full max-w-md rounded border border-slate-300 bg-surface p-4 shadow-lg"
			role="dialog"
			aria-modal="true"
			aria-labelledby="confirm-dialog-title"
			aria-describedby="confirm-dialog-message"
			tabindex="-1"
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
		</div>
	</div>
{/if}
