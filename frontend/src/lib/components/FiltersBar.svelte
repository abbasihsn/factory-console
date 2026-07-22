<script lang="ts">
	import { onDestroy } from 'svelte';

	interface Filters {
		status: string;
		track: string;
		milestone: string;
		q: string;
	}

	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with supplied props. Navigation is injected via
	// `onNavigate` (the route wires it to `goto`), mirroring TopBar's `onReload`.
	// `filters` is the URL-driven current selection; the option lists are derived
	// by the page from the loaded tickets.
	let {
		filters,
		statuses,
		tracks,
		milestones,
		onNavigate
	}: {
		filters: Filters;
		statuses: string[];
		tracks: string[];
		milestones: string[];
		onNavigate: (search: string) => void;
	} = $props();

	// Debounce the free-text search so each keystroke doesn't navigate (and trigger
	// a backend round-trip); the selects navigate immediately on change.
	const SEARCH_DEBOUNCE_MS = 250;

	// Local mirror of the search term, seeded from the URL-driven `q` and re-synced
	// whenever it changes (e.g. a reset navigation clears it) so the box never
	// drifts from the URL. The seed is read inside the effect (not the `$state`
	// initializer) so it tracks every `filters.q` change, not just the first.
	let searchTerm = $state('');
	$effect(() => {
		searchTerm = filters.q;
	});

	let debounceTimer: ReturnType<typeof setTimeout> | undefined;

	// Build the query string from the given filter set, omitting empty values, and
	// hand it to `onNavigate` WITHOUT a leading `?` (the caller adds it).
	function navigate(next: Filters): void {
		const params = new URLSearchParams();
		for (const [key, value] of Object.entries(next)) {
			if (value !== '') {
				params.set(key, value);
			}
		}
		onNavigate(params.toString());
	}

	// A select change navigates immediately; cancel any pending search debounce and
	// carry the currently-typed search term into this navigation.
	function selectFilter(patch: Partial<Filters>): void {
		clearTimeout(debounceTimer);
		navigate({
			status: filters.status,
			track: filters.track,
			milestone: filters.milestone,
			q: searchTerm,
			...patch
		});
	}

	function onSearchInput(event: Event): void {
		searchTerm = (event.currentTarget as HTMLInputElement).value;
		clearTimeout(debounceTimer);
		debounceTimer = setTimeout(() => {
			navigate({
				status: filters.status,
				track: filters.track,
				milestone: filters.milestone,
				q: searchTerm
			});
		}, SEARCH_DEBOUNCE_MS);
	}

	// Don't let a pending debounce fire a navigation after the component unmounts.
	onDestroy(() => clearTimeout(debounceTimer));

	const SELECT_CLASS = 'rounded border border-slate-300 bg-surface px-2 py-1 text-sm text-text';
</script>

<div class="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-surface px-4 py-3">
	<label class="flex flex-col gap-1 text-xs text-muted">
		Status
		<select
			class={SELECT_CLASS}
			aria-label="Filter by status"
			value={filters.status}
			onchange={(event) => selectFilter({ status: event.currentTarget.value })}
		>
			<option value="">All statuses</option>
			{#each statuses as option (option)}
				<option value={option}>{option}</option>
			{/each}
		</select>
	</label>

	<label class="flex flex-col gap-1 text-xs text-muted">
		Track
		<select
			class={SELECT_CLASS}
			aria-label="Filter by track"
			value={filters.track}
			onchange={(event) => selectFilter({ track: event.currentTarget.value })}
		>
			<option value="">All tracks</option>
			{#each tracks as option (option)}
				<option value={option}>{option}</option>
			{/each}
		</select>
	</label>

	<label class="flex flex-col gap-1 text-xs text-muted">
		Milestone
		<select
			class={SELECT_CLASS}
			aria-label="Filter by milestone"
			value={filters.milestone}
			onchange={(event) => selectFilter({ milestone: event.currentTarget.value })}
		>
			<option value="">All milestones</option>
			{#each milestones as option (option)}
				<option value={option}>{option}</option>
			{/each}
		</select>
	</label>

	<label class="flex flex-1 flex-col gap-1 text-xs text-muted">
		Search
		<input
			type="search"
			class={SELECT_CLASS}
			aria-label="Search tickets"
			placeholder="Search tickets…"
			value={searchTerm}
			oninput={onSearchInput}
		/>
	</label>
</div>
