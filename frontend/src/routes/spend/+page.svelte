<script lang="ts">
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const spend = $derived(data.spend);

	// `byTicket`/`byModel`/`byLevel`/`skipped` are optional in the generated schema
	// (they carry server-side defaults), so each is read through `?? []`.
	const byTicket = $derived(spend.byTicket ?? []);
	const byModel = $derived(spend.byModel ?? []);
	const byLevel = $derived(spend.byLevel ?? []);
	const skipped = $derived(spend.skipped ?? []);

	// The whole page keys off `source.found`, NEVER off the total being zero: a
	// fresh clone has no ledger (`.factory/` is gitignored) and rendering "$0.00"
	// there would be a false claim about real money. See T82's `SourceInfo`.
	const hasLedger = $derived(spend.source.found);

	// A ledger that was found but never opened (over the reader's size cap, or
	// unreadable) reports zero entries exactly like an empty one — so its bill is
	// UNKNOWN, not partial by a countable number of lines.
	const unread = $derived(!spend.source.read);

	// Lines missing from the totals. Meaningful only for per-line failures; when
	// the whole file went unread T82 reports a single skip at line 0 standing for
	// an unknown number of lines, which is what `unread` above branches on.
	const skippedCount = $derived(skipped.length + spend.skippedOmitted);

	const money = new Intl.NumberFormat('en-US', {
		style: 'currency',
		currency: 'USD',
		minimumFractionDigits: 2,
		maximumFractionDigits: 2
	});
	const count = new Intl.NumberFormat('en-US');

	// Rounding happens ONCE, here at render: T82 already rounded at its boundary,
	// so these formatters only choose how many places to show.
	function usd(value: number): string {
		return money.format(value);
	}

	function tokens(value: number): string {
		return count.format(value);
	}
</script>

<div class="space-y-6">
	<h1 class="text-2xl font-semibold text-text">Spend</h1>

	{#if !hasLedger}
		<!-- The no-ledger case renders the explanation and NOTHING else: no totals,
		     no zeroed tables. A table of zeros is a claim, and this project has not
		     been measured at all. (T83's shared SourcesBanner does not exist yet, so
		     the explanation is inline here rather than in a second banner component.) -->
		<div class="space-y-2 rounded-lg border border-slate-200 bg-surface px-4 py-6">
			<p class="font-medium text-text">No spend ledger for this project.</p>
			<p class="text-sm text-muted">
				The console looked for it at
				{#if spend.source.path}
					<code class="font-mono text-text">{spend.source.path}</code>
				{:else}
					the project's <code class="font-mono text-text">.factory/</code> directory
				{/if}
				and found nothing there.
			</p>
			<p class="text-sm text-muted">
				<code class="font-mono">.factory/</code> is machine-local and gitignored, so a fresh clone has
				no ledger. That is not the same as having spent nothing — this project's cost is unknown here,
				not zero.
			</p>
		</div>
	{:else}
		<section class="space-y-2 rounded-lg border border-slate-200 bg-surface px-4 py-6">
			<p class="text-sm text-muted">Total spend</p>
			<p data-testid="spend-total" class="text-4xl font-semibold text-text">
				{usd(spend.totals.costUsd)}
			</p>
			{#if skipped.length > 0}
				<!-- Adjacent to the figure on purpose: a footnote elsewhere does not
				     travel with the number someone screenshots. -->
				<p data-testid="partial-total" class="text-sm font-medium text-amber-700">
					{#if unread}
						Partial total — the ledger was found but could not be read, so this figure measures
						nothing and the real cost is unknown.
					{:else}
						Partial total — {tokens(skippedCount)}
						{skippedCount === 1 ? 'ledger line' : 'ledger lines'} could not be read and are excluded from
						this figure.
					{/if}
				</p>
			{/if}
			<p class="text-sm text-muted">
				{tokens(spend.totals.entries)} ledger {spend.totals.entries === 1 ? 'entry' : 'entries'}
				· {tokens(spend.totals.tokens.total)} tokens ({tokens(spend.totals.tokens.input)} in,
				{tokens(spend.totals.tokens.output)} out, {tokens(spend.totals.tokens.cacheRead)} cache read,
				{tokens(spend.totals.tokens.cacheCreation)} cache creation)
			</p>
		</section>

		<section class="space-y-2">
			<h2 class="text-lg font-semibold text-text">By ticket</h2>
			<p class="text-sm text-muted">
				Attribution: <code data-testid="attribution" class="font-mono text-text"
					>{spend.attribution}</code
				>
				— an entry naming several tickets is charged in full to each, so this column can sum to more than
				the total above.
			</p>
			{#if byTicket.length === 0}
				<p class="rounded-lg border border-slate-200 bg-surface px-4 py-6 text-center text-muted">
					No ticket spend recorded.
				</p>
			{:else}
				<table class="w-full overflow-hidden rounded-lg border border-slate-200 bg-surface text-sm">
					<thead class="border-b border-slate-200 text-left text-muted">
						<tr>
							<th class="px-4 py-2 font-medium">Ticket</th>
							<th class="px-4 py-2 text-right font-medium">Attributed cost</th>
							<th class="px-4 py-2 text-right font-medium">Entries</th>
							<th class="px-4 py-2 font-medium">Models</th>
						</tr>
					</thead>
					<tbody class="divide-y divide-slate-200">
						{#each byTicket as row (row.ticketId)}
							<tr>
								<td class="px-4 py-2 font-mono text-text">{row.ticketId}</td>
								<td class="px-4 py-2 text-right text-text">{usd(row.attributedCostUsd)}</td>
								<td class="px-4 py-2 text-right text-text">{tokens(row.entries)}</td>
								<td class="px-4 py-2 font-mono text-muted">{(row.models ?? []).join(', ')}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</section>

		<section class="space-y-2">
			<h2 class="text-lg font-semibold text-text">By model</h2>
			{#if byModel.length === 0}
				<p class="rounded-lg border border-slate-200 bg-surface px-4 py-6 text-center text-muted">
					No model spend recorded.
				</p>
			{:else}
				<table class="w-full overflow-hidden rounded-lg border border-slate-200 bg-surface text-sm">
					<thead class="border-b border-slate-200 text-left text-muted">
						<tr>
							<th class="px-4 py-2 font-medium">Model</th>
							<th class="px-4 py-2 text-right font-medium">Cost</th>
							<th class="px-4 py-2 text-right font-medium">Tokens</th>
						</tr>
					</thead>
					<tbody class="divide-y divide-slate-200">
						{#each byModel as row (row.model)}
							<tr>
								<!-- The model id VERBATIM as the factory wrote it. A model the console
								     has not heard of must be visible as itself, never bucketed. -->
								<td class="px-4 py-2 font-mono text-text">{row.model}</td>
								<td class="px-4 py-2 text-right text-text">{usd(row.costUsd)}</td>
								<td class="px-4 py-2 text-right text-text">{tokens(row.tokens.total)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</section>

		<section class="space-y-2">
			<h2 class="text-lg font-semibold text-text">By level</h2>
			{#if byLevel.length === 0}
				<p class="rounded-lg border border-slate-200 bg-surface px-4 py-6 text-center text-muted">
					No level spend recorded.
				</p>
			{:else}
				<table class="w-full overflow-hidden rounded-lg border border-slate-200 bg-surface text-sm">
					<thead class="border-b border-slate-200 text-left text-muted">
						<tr>
							<th class="px-4 py-2 font-medium">Level</th>
							<th class="px-4 py-2 text-right font-medium">Cost</th>
							<th class="px-4 py-2 text-right font-medium">Entries</th>
						</tr>
					</thead>
					<tbody class="divide-y divide-slate-200">
						{#each byLevel as row (row.level)}
							<tr>
								<td class="px-4 py-2 font-mono text-text">{row.level}</td>
								<td class="px-4 py-2 text-right text-text">{usd(row.costUsd)}</td>
								<td class="px-4 py-2 text-right text-text">{tokens(row.entries)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</section>
	{/if}
</div>
