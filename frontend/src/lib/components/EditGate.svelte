<script lang="ts">
	import type { RunState } from '$lib/api';
	import { isEditable } from '$lib/forms/editability';

	// Presentational only: no `$app/*` imports and no fetch, so it renders
	// deterministically under vitest/jsdom with a supplied run-state.
	//
	// The UI mirror of the server write-gate: it explains WHY the edit/delete
	// affordances beside it are inert. It shares the one predicate
	// (`isEditable`) with the buttons it explains, so the banner and the disabled
	// state can never disagree.
	let { runState }: { runState: RunState } = $props();

	const readOnly = $derived(!isEditable(runState));
</script>

{#if readOnly}
	<!-- `note` (not `alert`): nothing failed and nothing is time-sensitive — this is
	     standing context about the ticket, so it must not interrupt a screen reader. -->
	<div
		role="note"
		class="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
	>
		<span class="font-semibold">Read-only.</span>
		<!-- The RAW run-state value, not the humanized label: this sentence sits next
		     to the header's `RunStateBadge`, which already carries the label, and the
		     raw value is what the server gate and the run-state directory speak. -->
		This ticket's run-state is <span class="font-mono">{runState}</span>, so editing and deleting
		are disabled —
		<!-- `absent` needs its OWN reason (T80): no lane owns such a ticket, the
		     resolved run-state source simply never names it, so the lane-ownership
		     sentence would send the operator looking for a lane that does not exist.
		     Mirrors the server's `TicketNotMutable` wording for the same state. -->
		{#if runState === 'absent'}
			the project's run-state source does not list this ticket, so the console will not write it.
		{:else}
			a factory lane owns a ticket once it leaves <span class="font-mono">todo</span>.
		{/if}
		The server enforces the same gate and would reject the write anyway.
	</div>
{/if}
