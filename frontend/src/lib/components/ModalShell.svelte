<script lang="ts">
	import type { Snippet } from 'svelte';

	// The one modal shell: backdrop, Escape, focus management and the
	// `role="dialog"` wiring live here so every dialog behaves identically and a
	// fix lands once. Callers supply only their own body.
	//
	// Presentational only: no `$app/*` imports and no fetch, so it renders
	// deterministically under vitest/jsdom. Closing is the caller's decision —
	// this component only reports it through `onCancel`.
	let {
		open,
		labelledBy,
		describedBy,
		panelClass,
		onCancel,
		children
	}: {
		open: boolean;
		/** Id of the element naming the dialog. Required: `aria-modal` without a name is unusable. */
		labelledBy: string;
		/** Id of the element describing the dialog, when it has one. */
		describedBy?: string;
		/** Size/layout classes for the panel — the only part that differs per dialog. */
		panelClass: string;
		onCancel: () => void;
		children: Snippet;
	} = $props();

	let wrapper = $state<HTMLElement | null>(null);
	let panel = $state<HTMLElement | null>(null);

	// Everything the browser lets you Tab to, minus what is explicitly removed
	// from the tab order.
	const FOCUSABLE =
		'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

	function focusableItems(): HTMLElement[] {
		return Array.from(wrapper?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
	}

	// Move focus into the dialog when it opens so Escape and the buttons are
	// reachable without a mouse, and hand it back to whatever opened the dialog
	// when it closes — otherwise focus falls to `<body>` and the next Tab
	// restarts from the top of the document. The cleanup runs on close and on
	// unmount, which are the two ways a dialog goes away.
	$effect(() => {
		if (!open) return;
		const opener = document.activeElement;
		panel?.focus();
		// `activeElement` is an `Element`, which has no `focus()`. Narrow rather
		// than assert — and include SVG, which is focusable with a `tabindex`.
		return () => {
			if (opener instanceof HTMLElement || opener instanceof SVGElement) opener.focus();
		};
	});

	// `aria-modal="true"` promises the rest of the page is unavailable. Nothing
	// marks it `inert`, so without this Tab would walk straight out of the
	// dialog and let a keyboard user operate the page behind it — including the
	// action being confirmed. Cycling within the wrapper keeps that promise.
	function trapTab(event: KeyboardEvent): void {
		const items = focusableItems();
		if (items.length === 0) {
			// Nothing to move to: hold focus here rather than let it walk out.
			event.preventDefault();
			return;
		}

		const first = items[0];
		const last = items[items.length - 1];
		const active = document.activeElement;

		if (event.shiftKey) {
			// The panel counts as the start of the cycle: it is where focus lands
			// on open, and it sits ahead of its own contents.
			if (active === first || active === panel) {
				event.preventDefault();
				last.focus();
			}
			return;
		}

		// Only intervene where the default would leave the dialog. From the panel
		// itself the default is already correct — it steps into the panel's own
		// controls — unless the panel has none, in which case Tab would escape.
		const escapes =
			active === last ||
			(panel !== null && active === panel && panel.querySelector(FOCUSABLE) === null);
		if (escapes) {
			event.preventDefault();
			first.focus();
		}
	}

	// `<svelte:window>` may only sit at the top level, so the listener is always
	// attached and the closed case is rejected here instead. Keys are only ours
	// to act on while focus is inside this dialog — that is also what makes
	// stacked dialogs work, since only the topmost one holds focus.
	function handleKeydown(event: KeyboardEvent): void {
		if (!open) return;
		if (!wrapper?.contains(document.activeElement)) return;

		if (event.key === 'Escape') {
			event.preventDefault();
			onCancel();
			return;
		}
		if (event.key === 'Tab') trapTab(event);
	}
</script>

<svelte:window onkeydown={handleKeydown} />

{#if open}
	<div bind:this={wrapper} class="fixed inset-0 z-50 flex items-center justify-center p-4">
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
			class="relative rounded border border-slate-300 bg-surface shadow-lg {panelClass}"
			role="dialog"
			aria-modal="true"
			aria-labelledby={labelledBy}
			aria-describedby={describedBy}
			tabindex="-1"
		>
			{@render children()}
		</div>
	</div>
{/if}
