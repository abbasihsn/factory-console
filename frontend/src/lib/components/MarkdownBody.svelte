<script lang="ts">
	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with supplied props. `html` is `Ticket.bodyHtml`, which
	// the server renders and sanitizes per the REST v1 contract (T14).
	let { html }: { html: string } = $props();
</script>

<!-- REST v1 contract: server-rendered, server-sanitized HTML only (T14). Do NOT introduce a client-side markdown lib. This is the SPA's sole `@html` injection point — its one audited trust boundary. -->
<!-- eslint-disable-next-line svelte/no-at-html-tags -- sanctioned sole trust boundary; HTML is sanitized server-side per REST v1 (T14) -->
<div class="prose max-w-none">{@html html}</div>
