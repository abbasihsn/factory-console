<script lang="ts">
	import type { RunState } from '$lib/api';

	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with a supplied run-state. Each state maps to a colored
	// pill, a humanized label, and a short tooltip explaining what it means.
	//
	// The prop is typed against the generated `RunState`, whose values are the
	// names their SOURCE uses: the legacy marker directory names `in-flight`
	// (hyphenated), while the factory's run-state.json names `in_progress`,
	// `in_part`, `in_submilestone`, `flagged`, `failed` and `needs_human`
	// (underscored). These maps are `Record<RunState, …>`, so a state added to the
	// backend enum and regenerated into the type fails the build here rather than
	// rendering an unstyled, untitled, empty pill.
	let { runState }: { runState: RunState } = $props();

	// Full literal Tailwind class strings: the JIT scanner only sees complete
	// class strings, so these must never be built dynamically (e.g. `bg-${c}-100`)
	// or they get purged and the badge ships unstyled.
	//
	// Color families carry the meaning at a glance: amber = a lane is working on
	// it, red = the lane stopped and something is wrong (the states an operator
	// most needs to spot), green/violet = done, gray/slate = not started/unknown.
	const STATE_CLASSES: Record<RunState, string> = {
		todo: 'bg-gray-100 text-gray-800',
		'in-flight': 'bg-amber-100 text-amber-800',
		in_progress: 'bg-amber-100 text-amber-800',
		in_part: 'bg-amber-50 text-amber-700',
		in_submilestone: 'bg-amber-50 text-amber-700',
		ready: 'bg-green-100 text-green-800',
		merged: 'bg-violet-100 text-violet-800',
		flagged: 'bg-red-100 text-red-800',
		failed: 'bg-red-200 text-red-900',
		needs_human: 'bg-red-100 text-red-900 ring-1 ring-red-400',
		unknown: 'bg-slate-100 text-slate-500',
		absent: 'bg-slate-200 text-slate-600',
		// Red, unlike its slate siblings: `unknown` and `absent` are ordinary answers
		// about a source that WAS read, while `unreadable` is a source the console
		// could not read at all — an operator has to notice it, because every write in
		// the project is refused until it is fixed. Outlined rather than solid so it
		// still reads as "the console cannot see", not as a lane failure like
		// `failed`/`needs_human`.
		unreadable: 'bg-red-50 text-red-800 ring-1 ring-red-300'
	};
	const STATE_LABELS: Record<RunState, string> = {
		todo: 'To do',
		'in-flight': 'In flight',
		in_progress: 'In progress',
		in_part: 'In part',
		in_submilestone: 'In submilestone',
		ready: 'Ready',
		merged: 'Merged',
		flagged: 'Flagged',
		failed: 'Failed',
		needs_human: 'Needs human',
		unknown: 'Unknown',
		absent: 'Not listed',
		unreadable: 'Unreadable'
	};
	const STATE_TITLES: Record<RunState, string> = {
		todo: 'Queued — no factory lane has started this ticket yet',
		'in-flight': 'A factory lane is actively building this ticket',
		in_progress: 'A factory lane is actively building this ticket',
		in_part: 'Part of the ticket has landed — the lane is still working',
		in_submilestone: 'Being built as part of a submilestone run',
		ready: 'Built and reviewed — the PR is ready to merge',
		merged: 'The ticket PR has been merged',
		flagged: 'The lane finished but flagged a problem — needs a look',
		failed: 'The lane failed — the ticket did not get built',
		needs_human: 'Blocked: the factory cannot proceed without a human decision',
		// NOT just "no source": the server also answers `unknown` when a source
		// vanished, when its content could not be parsed, and when it lists this
		// ticket under a status this console does not recognise. Naming only the
		// first case would tell an operator with a corrupt run-state.json that they
		// have no run-state at all — hiding the degradation the state exists to report.
		unknown: 'No run-state source for this project, or its source could not be understood',
		absent: 'A run-state source exists but does not list this ticket',
		// The tooltip has to carry the FIX, because this is the only state whose cause
		// is on the operator's side: the source is there, the console cannot open it,
		// and every write is refused until that changes.
		unreadable:
			'The run-state source could not be read (check its permissions) — writes are refused'
	};
</script>

<span
	class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium {STATE_CLASSES[
		runState
	]}"
	title={STATE_TITLES[runState]}
>
	{STATE_LABELS[runState]}
</span>
