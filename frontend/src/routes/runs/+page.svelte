<script lang="ts">
	import type { PageData } from './$types';
	import type { ArtifactRead, ArtifactSkipReason } from '$lib/api';
	import RunStateBadge from '$lib/components/RunStateBadge.svelte';
	import SourcesBanner from '$lib/components/SourcesBanner.svelte';

	let { data }: { data: PageData } = $props();

	const rows = $derived(data.rows);

	// The whole-page "this project has never been run here" case, derived rather
	// than read: `GET /runs` publishes no project-level sources block, so the only
	// honest statement of it is that EVERY record said `absent` for BOTH artifacts.
	//
	// Gated on a non-empty list on purpose. `[].every(…)` is `true`, and an empty
	// manifest is a different fact entirely — there are no tickets, not no run data —
	// which gets its own empty state below. A degraded read (`unreadable` and
	// friends) is also deliberately excluded: something IS there, so the sentence
	// "the console found nothing" would be false.
	const allAbsent = $derived(
		rows.length > 0 &&
			rows.every(
				(row) =>
					row.record !== null &&
					row.record.result.reason === 'absent' &&
					row.record.receipt.reason === 'absent'
			)
	);

	// The directories that were probed, taken from the first record's own artifact
	// paths — the server sends the file it looked for on the `absent` outcome too,
	// precisely so a human can act on it — rather than from a hardcoded layout.
	const firstRecord = $derived(rows.find((row) => row.record !== null)?.record ?? null);
	const resultsPath = $derived(parentDir(firstRecord?.result.path));
	const receiptsPath = $derived(parentDir(firstRecord?.receipt.path));

	// Server paths are POSIX, but the console also runs on Windows, where a
	// `pathlib.Path` serializes with backslashes; both separators are honoured so the
	// probed-directory sentence never degrades into the whole file path.
	function parentDir(path: string | undefined): string | null {
		if (path === undefined) {
			return null;
		}
		const cut = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
		return cut > 0 ? path.slice(0, cut) : null;
	}

	// `Record<ArtifactSkipReason, …>`, so a reason added to the backend enum and
	// regenerated into the type fails the build here rather than rendering an
	// unlabelled cell — the same rule `RunStateBadge` sets for `RunState`.
	const REASON_LABELS: Record<ArtifactSkipReason, string> = {
		absent: '—',
		unreadable: 'Unreadable',
		unparseable: 'Unparseable',
		too_large: 'Too large'
	};
	const REASON_TITLES: Record<ArtifactSkipReason, string> = {
		absent: 'The factory never wrote this artifact — the lane has not run this ticket here',
		unreadable:
			'The artifact could not be read at all, or its path could not be proven to stay inside the project — its contents, and even its existence, are unknown',
		unparseable: 'The artifact is there and is not a JSON object — it answered, unintelligibly',
		too_large: 'The artifact is over the reader’s size cap and was not read rather than short-read'
	};

	// `absent` is the ordinary state of a fresh clone; every other reason is a real
	// failed read of something that IS there. They must not look alike: a blank cell
	// for `unparseable` would report a corrupt artifact as a lane that never ran.
	function isDegraded(reason: ArtifactSkipReason | null | undefined): boolean {
		return reason !== null && reason !== undefined && reason !== 'absent';
	}

	// `data` is an untyped JSON object by contract — the backend models no field
	// inside a factory artifact because it has verified none — so every field is
	// read through a guard and a missing or non-string value is simply "not there".
	function readString(artifact: ArtifactRead, key: string): string | null {
		const value = artifact.data?.[key];
		return typeof value === 'string' && value.trim() !== '' ? value.trim() : null;
	}

	// The PR link, when the result names one. Rendered ONLY for an http(s) URL: the
	// value is whatever another process wrote into a local JSON file, so a `javascript:`
	// or `data:` href would be handed straight to the browser as a link the user clicks.
	function prUrl(artifact: ArtifactRead): string | null {
		const value = readString(artifact, 'pr_url');
		if (value === null) {
			return null;
		}
		try {
			const parsed = new URL(value);
			return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? value : null;
		} catch {
			return null;
		}
	}
</script>

{#snippet reasonCell(testid: string, reason: ArtifactSkipReason)}
	{#if isDegraded(reason)}
		<span
			data-testid={testid}
			data-degraded="true"
			class="inline-flex items-center rounded-full bg-red-50 px-2 py-0.5 text-xs font-medium text-red-800 ring-1 ring-red-300"
			title={REASON_TITLES[reason]}
		>
			{REASON_LABELS[reason]}
		</span>
	{:else}
		<span data-testid={testid} class="text-muted" title={REASON_TITLES[reason]}>
			{REASON_LABELS[reason]}
		</span>
	{/if}
{/snippet}

<div class="space-y-6">
	<h1 class="text-2xl font-semibold text-text">Runs</h1>

	{#if allAbsent}
		<!-- The no-run-data case renders the explanation and NOTHING else. A table of
		     77 rows with empty cells reads as "the factory ran and recorded nothing",
		     which is a claim about the factory made from a fact about this clone. -->
		<SourcesBanner {allAbsent} {resultsPath} {receiptsPath} />
	{:else if rows.length === 0}
		<p
			data-testid="empty-manifest"
			class="rounded-lg border border-slate-200 bg-surface px-4 py-6 text-center text-muted"
		>
			No tickets in this project's manifest.
		</p>
	{:else}
		<p class="text-sm text-muted">
			One row per ticket in the manifest — including tickets the factory has not run here, whose
			artifacts are named as missing rather than left blank.
		</p>
		<table class="w-full overflow-hidden rounded-lg border border-slate-200 bg-surface text-sm">
			<thead class="border-b border-slate-200 text-left text-muted">
				<tr>
					<th class="px-4 py-2 font-medium">Ticket</th>
					<th class="px-4 py-2 font-medium">Run state</th>
					<th class="px-4 py-2 font-medium">PR</th>
					<th class="px-4 py-2 font-medium">Outcome</th>
					<th class="px-4 py-2 font-medium">Receipt</th>
				</tr>
			</thead>
			<tbody class="divide-y divide-slate-200">
				{#each rows as row (row.ticketId)}
					{@const record = row.record}
					<tr data-testid="run-row-{row.ticketId}">
						<td class="px-4 py-2">
							<a href="/tickets/{row.ticketId}" class="font-mono text-text hover:underline"
								>{row.ticketId}</a
							>
							<span class="ml-2 text-muted">{row.title}</span>
						</td>
						<td class="px-4 py-2"><RunStateBadge runState={row.runState} /></td>
						{#if record === null}
							<!-- The two endpoints disagreed about the manifest. Saying so beats
							     rendering three cells of absence the response never asserted. -->
							<td class="px-4 py-2" colspan="3">
								<span
									data-testid="no-record-{row.ticketId}"
									data-degraded="true"
									class="inline-flex items-center rounded-full bg-red-50 px-2 py-0.5 text-xs font-medium text-red-800 ring-1 ring-red-300"
									title="The runs listing carries no record for this manifest ticket — the console's two reads disagree"
								>
									No run record
								</span>
							</td>
						{:else}
							{@const pr = prUrl(record.result)}
							{@const outcome = readString(record.result, 'status')}
							<td class="px-4 py-2">
								{#if pr}
									<a
										data-testid="pr-link-{row.ticketId}"
										href={pr}
										target="_blank"
										rel="noreferrer noopener"
										class="text-text underline">Pull request</a
									>
								{:else if record.result.reason}
									{@render reasonCell(`pr-${row.ticketId}`, record.result.reason)}
								{:else}
									<!-- The result WAS read and names no usable PR url. Distinct from an
									     unread artifact: this one answered. -->
									<span
										data-testid="pr-{row.ticketId}"
										class="text-muted"
										title="The result artifact was read and carries no PR url">—</span
									>
								{/if}
							</td>
							<td class="px-4 py-2">
								{#if record.result.reason}
									{@render reasonCell(`outcome-${row.ticketId}`, record.result.reason)}
								{:else if outcome}
									<!-- The outcome VERBATIM as the factory wrote it: the console models no
									     field inside an artifact, so an unrecognised value must show as
									     itself rather than be bucketed or dropped. -->
									<span data-testid="outcome-{row.ticketId}" class="font-mono text-text"
										>{outcome}</span
									>
								{:else}
									<span
										data-testid="outcome-{row.ticketId}"
										class="text-muted"
										title="The result artifact was read and names no status">—</span
									>
								{/if}
							</td>
							<td class="px-4 py-2">
								{#if record.receipt.reason}
									{@render reasonCell(`receipt-${row.ticketId}`, record.receipt.reason)}
								{:else}
									<span
										data-testid="receipt-{row.ticketId}"
										class="inline-flex items-center rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800"
										title={record.receipt.path}>Present</span
									>
								{/if}
							</td>
						{/if}
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</div>
