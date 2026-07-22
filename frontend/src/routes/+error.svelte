<script lang="ts">
	// Import the global stylesheet directly: when the root layout's `load`
	// throws, `+layout.svelte` never renders, so this boundary must pull in the
	// design tokens + Tailwind base itself to stay styled.
	import '../app.css';
	import { page } from '$app/state';
	import { invalidateAll } from '$app/navigation';
	import ApiErrorView from '$lib/components/ApiErrorView.svelte';
	import { normalizeError } from '$lib/api/contracts';

	// `page.error` is already an `ApiError` when we threw it, but normalize again
	// to cover SvelteKit's built-in errors (e.g. an unknown route's `{ message }`).
	const apiError = $derived(normalizeError(page.error));
</script>

<div class="min-h-screen bg-bg text-text">
	<ApiErrorView error={apiError} onReload={invalidateAll} />
</div>
