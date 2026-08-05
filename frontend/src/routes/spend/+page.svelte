<script lang="ts">
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const spend = $derived(data.spend);

	// `byTicket`/`byModel`/`byLevel`/`skipped` are optional in the generated schema
	// because they carry NO schema default: `openapi-typescript` marks a property that
	// HAS a default non-optional (the rule `models.ts` documents on `TicketCreate`'s
	// `provides`), which is why `totals`/`attribution`/`skippedOmitted`/`source.read`
	// are all required despite defaulting. So the server may omit these four keys
	// outright and each is read through `?? []`; the defaulted fields are read directly,
	// without a guard.
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
	// UNKNOWN, not partial by a countable number of lines. It therefore gets its
	// OWN top-level branch beside the no-ledger case rather than falling through to
	// the totals: the figure it would carry measures nothing.
	//
	// Read off `source.read` and never off `skipped`: the line-0 skip that names the
	// reason is T82's convention, not a schema guarantee, so keying on it would let
	// a body that omits it render a confident zero.
	const unread = $derived(!spend.source.read);

	// Lines missing from the totals. Meaningful only for per-line failures; when
	// the whole file went unread T82 reports a single skip at line 0 standing for
	// an unknown number of lines, which is what `unread` above branches on.
	//
	// It counts the materialised skips PLUS those past the reader's detail cap:
	// `skipped` alone would under-report a ledger whose failures all fell past the
	// cap, so this — not `skipped.length` — is what gates the partial-total notice.
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
	//
	// Two places is the money convention, but it maps every non-zero cost below
	// half a cent to "$0.00" — and a sub-cent row is ordinary here, since a cheap
	// model's share of a ticket can land there. That would be the same false "this
	// was free" claim the no-ledger branch exists to prevent, reached by rounding
	// rather than by missing data, so such a value renders as "<$0.01" instead. A
	// genuine zero still formats as "$0.00".
	function usd(value: number): string {
		return value > 0 && value < 0.005 ? '<$0.01' : money.format(value);
	}

	// Named for what it does, not for one of the things it counts: the same
	// thousands-separated formatting serves token totals, ledger entry counts and
	// skipped-line counts alike.
	function formatCount(value: number): string {
		return count.format(value);
	}
</script>

<div class="space-y-6">
	<h1 class="text-2xl font-semibold text-text">Spend</h1>

	{#if !hasLedger}
		<!-- The no-ledger case renders the explanation and NOTHING else: no totals,
		     no zeroed tables. A table of zeros is a claim, and this project has not
		     been measured at all. (T83 has since added `SourcesBanner`, but it says one
		     thing — "no factory run data", naming the two artifact directories it
		     probed — and cannot state this one, which is about the ledger. Reusing it
		     would need it generalized over its headline and probed paths first; until
		     then the explanation stays inline here rather than misreported by a
		     component that names the wrong source.) -->
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
	{:else if unread}
		<!-- The ledger EXISTS but could not be opened (over the reader's size cap, or
		     unreadable), so `totals` is a placeholder for a bill nobody counted — NOT a
		     measured zero. Rendering the figure here with a caveat under it would make
		     exactly the false claim about real money the no-ledger branch above refuses
		     to make, merely relocated: "$0.00" over "0 ledger entries" and three "No X
		     spend recorded." panels all assert a measurement that never happened. So
		     this branch, like that one, emits NO money figure and NO tables. See
		     `SourceInfo` in the generated types: `read` exists to carry precisely this
		     distinction one step past `found`. -->
		<div class="space-y-2 rounded-lg border border-slate-200 bg-surface px-4 py-6">
			<p data-testid="unread-ledger" class="font-medium text-amber-700">
				Spend unknown — the ledger could not be read.
			</p>
			<p class="text-sm text-muted">
				The console found a ledger
				{#if spend.source.path}
					at <code class="font-mono text-text">{spend.source.path}</code>
				{:else}
					in the project's <code class="font-mono text-text">.factory/</code> directory
				{/if}
				but could not open it — it is over the reader's size cap, or unreadable.
			</p>
			<p class="text-sm text-muted">
				Nothing was counted, so this project's cost is unknown here, not zero.
			</p>
		</div>
	{:else}
		<section class="space-y-2 rounded-lg border border-slate-200 bg-surface px-4 py-6">
			<p class="text-sm text-muted">Total spend</p>
			<p data-testid="spend-total" class="text-4xl font-semibold text-text">
				{usd(spend.totals.costUsd)}
			</p>
			{#if skippedCount > 0}
				<!-- Adjacent to the figure on purpose: a footnote elsewhere does not
				     travel with the number someone screenshots.

				     Gated on `skippedCount`, not `skipped.length`: a ledger whose failures
				     all fell past the reader's detail cap materialises NO skip entries but
				     still has lines missing from this figure, and that must not read as a
				     complete total. The whole-file case is not reachable here — it has its
				     own branch above. -->
				<p data-testid="partial-total" class="text-sm font-medium text-amber-700">
					Partial total — {formatCount(skippedCount)}
					{skippedCount === 1
						? 'ledger line could not be read and is'
						: 'ledger lines could not be read and are'} excluded from this figure.
				</p>
			{/if}
			<p class="text-sm text-muted">
				{formatCount(spend.totals.entries)} ledger {spend.totals.entries === 1
					? 'entry'
					: 'entries'}
				· {formatCount(spend.totals.tokens.total)} tokens ({formatCount(spend.totals.tokens.input)} in,
				{formatCount(spend.totals.tokens.output)} out,
				{formatCount(spend.totals.tokens.cacheRead)} cache read,
				{formatCount(spend.totals.tokens.cacheCreation)} cache creation)
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
								<td class="px-4 py-2 text-right text-text">{formatCount(row.entries)}</td>
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
								<td class="px-4 py-2 text-right text-text">{formatCount(row.tokens.total)}</td>
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
								<td class="px-4 py-2 text-right text-text">{formatCount(row.entries)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</section>
	{/if}
</div>
