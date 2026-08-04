<script lang="ts">
	// Presentational only: no `$app/*` imports and no fetching, so it renders
	// deterministically under vitest/jsdom from supplied props.
	//
	// It renders ONE case: the project has no factory run data at ALL. `GET /runs`
	// publishes no project-level `sources` block — every absence it reports is
	// per-ticket, per-artifact — so "this project has never been run here" is not a
	// field to read but a fact the caller derives: EVERY record's result AND receipt
	// said `absent`. The caller passes that conclusion in as `allAbsent`; this
	// component only says it well.
	//
	// The partial case is deliberately NOT a banner. When some tickets have
	// artifacts and others do not, the missing ones are ordinary un-run tickets, and
	// nothing in the response distinguishes that from a source-level problem beyond
	// the per-row `reason` the table already shows. A banner would have to guess.
	let {
		allAbsent,
		resultsPath = null,
		receiptsPath = null
	}: {
		allAbsent: boolean;
		// The directories that were probed, taken from the records' own artifact
		// paths, so the explanation names where the console actually looked rather
		// than repeating a hardcoded layout. Null when there is no record to read a
		// path from (an empty manifest), in which case the relative names are used.
		resultsPath?: string | null;
		receiptsPath?: string | null;
	} = $props();
</script>

{#if allAbsent}
	<div
		data-testid="no-run-data"
		class="space-y-2 rounded-lg border border-slate-200 bg-surface px-4 py-6"
	>
		<p class="font-medium text-text">No factory run data in this project.</p>
		<p class="text-sm text-muted">
			The console probed
			<code class="font-mono text-text">{resultsPath ?? '.factory/results'}</code>
			and
			<code class="font-mono text-text">{receiptsPath ?? '.factory/receipts'}</code>
			for every ticket in the manifest and found no artifact at either.
		</p>
		<p class="text-sm text-muted">
			<code class="font-mono">.factory/</code> is machine-local and gitignored, so a fresh clone has
			no run data. That is not the same as the factory having run and recorded nothing — nothing has
			been recorded <em>here</em>, and these tickets' outcomes are unknown on this machine rather
			than empty.
		</p>
	</div>
{/if}
