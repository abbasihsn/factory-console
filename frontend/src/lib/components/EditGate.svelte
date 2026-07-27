<script lang="ts">
	import type { RunState } from '$lib/api';
	import { isEditable } from '$lib/forms/editability';

	// Presentational only: no `$app/*` imports and no fetch, so it renders
	// deterministically under vitest/jsdom. It is the VISIBLE half of the
	// client-side mirror of the server write-gate (`isEditable`) — it explains why
	// the edit/delete affordances next to it are inert. It never gates anything
	// itself: the server's `write_gate` is the real one.
	let { runState }: { runState: RunState } = $props();

	// Why each non-editable state is immutable. Covers EXACTLY the states
	// `isEditable` rejects — hence `Partial`: `todo`/`unknown` have no reason to
	// give because they are editable, and the `{#if}` below never asks for one.
	const IMMUTABLE_REASON: Partial<Record<RunState, string>> = {
		'in-flight': 'a factory lane is building it right now',
		ready: 'its PR is built and waiting to merge',
		merged: 'its PR has already been merged'
	};

	const reason = $derived(isEditable(runState) ? null : IMMUTABLE_REASON[runState]);
</script>

{#if reason}
	<!-- `role="status"` (polite): the banner appears alongside disabled buttons, so
	     a screen-reader user learns the reason without being interrupted. -->
	<div
		role="status"
		class="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
	>
		Read-only: this ticket is <span class="font-mono">{runState}</span> — {reason}. Only
		<span class="font-mono">todo</span> tickets can be edited or deleted.
	</div>
{/if}
