<script lang="ts">
	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with supplied items. Renders a wrapping row of chips shared
	// by the detail route's `dependsOn` (each a link) and `provides` (plain) lists:
	// an item with an `href` becomes a link chip, otherwise a plain span chip.
	let { items }: { items: { label: string; href?: string }[] } = $props();

	// Base chip styling mirrors TicketRow's CHIP_CLASS — kept as complete literal
	// strings so the Tailwind JIT never purges them. Link chips swap the muted text
	// for the accent + hover underline the index links use; plain chips stay muted.
	const CHIP_BASE = 'rounded-full bg-slate-100 px-2 py-0.5 text-xs';
	const LINK_CHIP_CLASS = `${CHIP_BASE} text-accent hover:underline`;
	const PLAIN_CHIP_CLASS = `${CHIP_BASE} text-slate-600`;
</script>

<div class="flex flex-wrap gap-2">
	{#each items as item (item.label)}
		{#if item.href}
			<a href={item.href} class={LINK_CHIP_CLASS}>{item.label}</a>
		{:else}
			<span class={PLAIN_CHIP_CLASS}>{item.label}</span>
		{/if}
	{/each}
</div>
