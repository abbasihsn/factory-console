<script lang="ts">
	import type { RunState } from '$lib/api';
	import { isDeletable, isEditable } from '$lib/forms/editability';

	// Presentational only: no `$app/*` imports and no fetch, so it renders
	// deterministically under vitest/jsdom with a supplied run-state.
	//
	// The UI mirror of the server write-gate: it explains WHY the edit/delete
	// affordances beside it are inert. It shares its predicates (`isEditable`,
	// `isDeletable`) with the buttons it explains, so the banner and the disabled
	// state can never disagree — which since T80 means mirroring BOTH server
	// allowlists, because `absent` disables Edit while leaving Delete enabled.
	let { runState }: { runState: RunState } = $props();

	const readOnly = $derived(!isEditable(runState));
	const deletable = $derived(isDeletable(runState));
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
		<!-- WHICH writes are disabled is not uniform (T80): `absent` refuses the edit
		     but permits the delete, so this clause must follow `isDeletable` rather
		     than assert both. -->
		This ticket's run-state is <span class="font-mono">{runState}</span>, so
		{#if deletable}editing is disabled{:else}editing and deleting are disabled{/if} —
		<!-- `absent` and `unreadable` each need their OWN reason (T80 + amendment 2): no
		     lane owns such a ticket — the resolved run-state source never names it, or
		     could not be read at all — so the lane-ownership sentence would send the
		     operator looking for a lane that does not exist. Each mirrors the server's
		     `TicketNotMutable` wording for the same state, and `unreadable` must point at
		     the SOURCE rather than the ticket's tracking status, because that is where
		     the fix is.

		     `unreadable` has TWO causes since amendment 4 — the source could not be
		     opened, or it was read and what it says about this ticket could not be
		     interpreted — and this banner cannot tell them apart, having only the enum
		     member. So it must not assert "fix the permissions": for the second cause
		     that is the wrong fix, and sends the operator to chmod a file that reads
		     perfectly well. The server's 409 carries the specific cause and names the
		     offending value; the banner's job is only to explain why the buttons beside
		     it are inert. -->
		{#if runState === 'absent'}
			the project's run-state source does not list this ticket, so the console will not edit it. You
			can still delete it.
		{:else if runState === 'unreadable'}
			the project's run-state source could not be read, or says something about this ticket that
			this console cannot interpret, so the console refuses every write to this ticket until that is
			resolved.
		{:else}
			a factory lane owns a ticket once it leaves <span class="font-mono">todo</span>.
		{/if}
		The server enforces the same gate and would reject the {deletable ? 'edit' : 'write'} anyway.
	</div>
{/if}
