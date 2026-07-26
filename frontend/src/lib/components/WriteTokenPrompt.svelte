<script lang="ts">
	import { setToken } from '$lib/stores/writeToken';

	// Presentational: no `$app/*` imports and no fetch, so it renders
	// deterministically under vitest/jsdom. Its only effect is `setToken` — the
	// session store the write wrappers read their token from — and an optional
	// `onSaved` so a host (a dialog, a route) can close or continue afterwards.
	let { onSaved }: { onSaved?: () => void } = $props();

	let pasted = $state('');

	// Blank (or whitespace-only) input is not a token: keep submit inert rather than
	// clearing a good stored token by "saving" nothing. `setToken` trims and
	// normalizes too — this is the UX half of the same rule, not its only guard.
	const canSubmit = $derived(pasted.trim().length > 0);

	function handleSubmit(event: SubmitEvent): void {
		event.preventDefault();
		// The button is disabled while blank, but a stray Enter must not slip through.
		if (!canSubmit) return;
		setToken(pasted);
		// Don't leave the secret sitting in the field once it is stored.
		pasted = '';
		onSaved?.();
	}
</script>

<form class="flex flex-col gap-2" onsubmit={handleSubmit}>
	<label class="flex flex-col gap-1 text-xs text-muted" for="write-token-input">
		Write token
	</label>
	<input
		id="write-token-input"
		type="password"
		class="rounded border border-slate-300 bg-surface px-2 py-1 text-sm text-text"
		aria-describedby="write-token-hint"
		autocomplete="off"
		spellcheck="false"
		placeholder="Paste the token"
		bind:value={pasted}
	/>
	<p id="write-token-hint" class="text-xs text-muted">
		The server prints this token to its own stderr at startup. Editing is disabled until it is
		entered; it is kept for this tab only.
	</p>
	<div>
		<button
			type="submit"
			class="rounded border border-slate-300 px-3 py-1 text-sm text-text hover:bg-bg disabled:cursor-not-allowed disabled:opacity-60"
			disabled={!canSubmit}
		>
			Save token
		</button>
	</div>
</form>
