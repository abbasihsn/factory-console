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

	// Read the live search-box value directly rather than mirroring it into local
	// state: the box is seeded one-way from the URL-driven `filters.q`, so a
	// reset/replace navigation updates it, while typing (which never touches
	// `filters.q`) leaves it alone between navigations. This keeps navigation
	// deterministic — no effect timing to race.
	let searchInput: HTMLInputElement | undefined;
	function currentSearch(): string {
		return searchInput?.value ?? filters.q;
	}

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

	// The four-field filter set behind the current selection: the URL-driven
	// selects plus the live (possibly just-typed) search term. Both navigation
	// paths build it here so the shape lives in exactly one place.
	function currentFilters(): Filters {
		return {
			status: filters.status,
			track: filters.track,
			milestone: filters.milestone,
			q: currentSearch()
		};
	}

	// A select change navigates immediately, carrying the currently-typed search
	// term; cancel any pending search debounce first (this navigation supersedes it).
	function selectFilter(patch: Partial<Filters>): void {
		clearTimeout(debounceTimer);
		navigate({ ...currentFilters(), ...patch });
	}

	function onSearchInput(): void {
		clearTimeout(debounceTimer);
		debounceTimer = setTimeout(() => {
			navigate(currentFilters());
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
			bind:this={searchInput}
			type="search"
			class={SELECT_CLASS}
			aria-label="Search tickets"
			placeholder="Search tickets…"
			value={filters.q}
			oninput={onSearchInput}
		/>
	</label>
</div>
