<script lang="ts">
	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with supplied items. Renders a wrapping row of chips shared
	// by the detail route's `dependsOn` (each a link) and `provides` (plain) lists:
	// an item with an `href` becomes a link chip, otherwise a plain span chip. A
	// repeated label is rendered once (see `distinctByLabel`).
	type ChipItem = { label: string; href?: string };

	let { items }: { items: ChipItem[] } = $props();

	// Chips are a set-like display fed straight from the API (`ticket.dependsOn`,
	// `ticket.provides`), and NOTHING upstream promises those values are distinct —
	// a repeated one would be a duplicate `{#each}` key and crash the page with
	// `each_key_duplicate`. De-dup by label here, first occurrence winning, so the
	// component is safe for ANY input rather than trusting the backend.
	function distinctByLabel(chips: ChipItem[]): ChipItem[] {
		const seen = new Set<string>();
		return chips.filter((chip) => {
			if (seen.has(chip.label)) return false;
			seen.add(chip.label);
			return true;
		});
	}

	const chips = $derived(distinctByLabel(items));

	// Base chip styling mirrors TicketRow's CHIP_CLASS — kept as complete literal
	// strings so the Tailwind JIT never purges them. Link chips swap the muted text
	// for the accent + hover underline the index links use; plain chips stay muted.
	const CHIP_BASE = 'rounded-full bg-slate-100 px-2 py-0.5 text-xs';
	const LINK_CHIP_CLASS = `${CHIP_BASE} text-accent hover:underline`;
	const PLAIN_CHIP_CLASS = `${CHIP_BASE} text-slate-600`;
</script>

<div class="flex flex-wrap gap-2">
	{#each chips as item (item.label)}
		{#if item.href}
			<a href={item.href} class={LINK_CHIP_CLASS}>{item.label}</a>
		{:else}
			<span class={PLAIN_CHIP_CLASS}>{item.label}</span>
		{/if}
	{/each}
</div>
