<script lang="ts">
	// Presentational only: no `$app/*` imports, no fetch — it renders deterministically
	// under vitest/jsdom with a supplied record.
	//
	// The one recurring human gate in App Factory v3. Tickets auto-merge onto a single
	// `factory/<sub-version>` branch as each lane finishes, and the factory then HOLDS
	// at that branch's PR and waits for a person. Until now the console — which exists
	// to be the human's window onto the factory — showed nothing of the one thing the
	// factory stops for, so an operator had to read run-state.json to find out what was
	// being waited on, or that anything was.
	import type { Subversion } from '$lib/api';

	let { subversion }: { subversion: Subversion | null | undefined } = $props();

	// Cut, but no PR opened yet. The factory records the branch when it CUTS the
	// sub-version and the url only once `ai-gh-open-pr` has run, so these are two
	// genuinely different situations and the strip must not show them the same way:
	// no url means the factory is still BUILDING into the branch and nothing is being
	// asked of anyone, while a url means it is WAITING and the next move is a human's.
	const waiting = $derived(Boolean(subversion?.prUrl));
</script>

<!-- Absent is the NORMAL state, not an error: the factory deletes the record when the
     branch lands on main, so a healthy project has no open sub-version most of the
     time. Rendering nothing — rather than an empty or "none open" strip — is what
     keeps the bar meaning "something is open" whenever it is on screen at all. -->
{#if subversion}
	<div
		class="border-b px-4 py-2 text-sm {waiting
			? 'border-amber-200 bg-amber-50 text-amber-900'
			: 'border-slate-200 bg-bg text-muted'}"
	>
		<div class="mx-auto flex max-w-5xl flex-wrap items-center gap-x-3 gap-y-1">
			<span class="font-medium">
				{#if waiting}
					Sub-version waiting to merge
				{:else}
					Sub-version in progress
				{/if}
			</span>
			{#if subversion.name}
				<span class="font-mono">{subversion.name}</span>
			{/if}
			<span class="font-mono text-xs opacity-80">{subversion.branch}</span>
			{#if subversion.prUrl}
				<!-- The PR is on GitHub, so this is the one link in the shell that leaves the
				     console. `rel="noreferrer"` alongside `noopener` because the target is
				     read out of the project's run-state file: the console does not own that
				     value, and a referrer is worth withholding from a url it did not mint. -->
				<a
					class="text-accent hover:underline"
					href={subversion.prUrl}
					target="_blank"
					rel="noopener noreferrer">Review the PR →</a
				>
			{:else}
				<span class="text-xs opacity-80">no PR opened yet</span>
			{/if}
		</div>
	</div>
{/if}
