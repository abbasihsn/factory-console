<script lang="ts">
	import { onDestroy } from 'svelte';
	import type { Filters } from '$lib/api';

	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with supplied props. Navigation is injected via
	// `onNavigate` (the route wires it to `goto`), mirroring TopBar's `onReload`;
	// the component hands up a resolved `Filters` object and lets the route own the
	// URL serialization, so both halves of the URL contract live in one layer.
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
		onNavigate: (next: Filters) => void;
	} = $props();

	// Debounce the free-text search so each keystroke doesn't navigate (and trigger
	// a backend round-trip); the selects navigate immediately on change.
	const SEARCH_DEBOUNCE_MS = 250;

	// Read the live search-box value directly rather than mirroring it into local
	// state: the box is seeded one-way from the URL-driven `filters.q` and typing
	// (which never touches `filters.q`) leaves it alone between navigations, so the
	// navigation build reads the DOM as the single source of the typed term.
	let searchInput: HTMLInputElement | undefined;
	function currentSearch(): string {
		return searchInput?.value ?? filters.q;
	}

	let debounceTimer: ReturnType<typeof setTimeout> | undefined;

	// Reading `filters` (a fresh object each load) makes this rerun on every
	// navigation, including a reset that leaves `filters.q` unchanged. When the box
	// is NOT focused, the navigation came from outside it — e.g. the empty state's
	// "clear filters" link — so cancel any still-pending debounce and re-sync the
	// box to the URL term; otherwise a mid-typed value would survive the one-way
	// `value=` seed and the stale timer would re-apply it as `?q=…` after the reset.
	// While the box IS focused the user is mid-edit, so leave their term and its
	// own debounce alone (the search box owns the live term between navigations).
	$effect(() => {
		const seed = filters.q;
		if (searchInput && searchInput !== document.activeElement) {
			clearTimeout(debounceTimer);
			if (searchInput.value !== seed) {
				searchInput.value = seed;
			}
		}
	});

	// The four-field filter set behind the current selection: the URL-driven
	// selects plus the live (possibly just-typed) search term. Both navigation
	// paths build it here so the shape lives in exactly one place, then hand it to
	// `onNavigate`; the route owns turning it into a URL, so this stays presentational.
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
		onNavigate({ ...currentFilters(), ...patch });
	}

	function onSearchInput(): void {
		clearTimeout(debounceTimer);
		debounceTimer = setTimeout(() => {
			onNavigate(currentFilters());
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
