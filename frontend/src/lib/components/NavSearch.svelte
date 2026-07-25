<script lang="ts">
	// The single navigation surface for the header: the /graph + /roadmap (and
	// Home) links plus the full-text search box. This component OWNS navigation —
	// it is the only header piece that imports `$app/navigation` — so `TopBar`
	// stays presentational and `$app`-free (and keeps unit-testing under jsdom).
	import { goto } from '$app/navigation';

	let query = $state('');

	// Submit → /search?q=<encoded>. The trimmed term is the source of truth; an
	// empty box still navigates to /search?q= so the results page shows its empty
	// state (the loader short-circuits the API call on an empty q). `goto` is given
	// the already-encoded string, so the query is encoded exactly once.
	function submit(event: SubmitEvent): void {
		event.preventDefault();
		goto(`/search?q=${encodeURIComponent(query.trim())}`);
	}
</script>

<nav class="flex items-center gap-4">
	<a href="/" class="text-sm text-text hover:underline">Home</a>
	<a href="/graph" class="text-sm text-text hover:underline">Graph</a>
	<a href="/roadmap" class="text-sm text-text hover:underline">Roadmap</a>
	<form class="flex items-center" onsubmit={submit}>
		<input
			type="search"
			aria-label="Search tickets"
			placeholder="Search tickets"
			bind:value={query}
			class="rounded border border-slate-300 px-2 py-1 text-sm text-text"
		/>
	</form>
</nav>
