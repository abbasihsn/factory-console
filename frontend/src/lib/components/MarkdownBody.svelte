<script lang="ts">
	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with supplied props. This is the SINGLE `@html` trust
	// boundary in the whole SPA; `html` is the server-sanitized `Ticket.bodyHtml`.
	let { html }: { html: string } = $props();
</script>

<div class="prose max-w-none">
	<!-- eslint-disable-next-line svelte/no-at-html-tags -- REST v1 contract: Server-rendered HTML only; do NOT introduce a client-side markdown lib. -->
	{@html html}
</div>
