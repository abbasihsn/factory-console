<script lang="ts">
	import type { RunState } from '$lib/api/models';

	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with supplied props.
	let { runState }: { runState: RunState } = $props();

	// One entry per RunState. Keys MUST be the exact hyphenated union values from
	// the generated types (note `in-flight`, not `in_flight`); `title` is a short
	// human explanation surfaced as a tooltip.
	const STATES: Record<RunState, { color: string; title: string }> = {
		todo: {
			color: 'bg-gray-100 text-gray-700',
			title: 'Not started — no run-state directory yet.'
		},
		'in-flight': {
			color: 'bg-amber-100 text-amber-800',
			title: 'A team-lead lane is actively building this ticket.'
		},
		ready: {
			color: 'bg-green-100 text-green-800',
			title: 'Built and passing — ready to review and merge.'
		},
		merged: {
			color: 'bg-indigo-100 text-indigo-800',
			title: 'Merged into the main branch.'
		},
		unknown: {
			color: 'bg-gray-100 text-muted',
			title: 'Run-state could not be determined.'
		}
	};
	const PILL = 'inline-flex items-center rounded px-2 py-0.5 text-xs font-medium';

	let state = $derived(STATES[runState]);
</script>

<span class="{PILL} {state.color}" title={state.title}>{runState}</span>
