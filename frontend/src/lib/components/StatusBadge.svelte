<script lang="ts">
	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with supplied props. A known ticket status maps to a
	// colored pill; an unknown status falls back to a neutral pill showing the
	// raw string. The raw status text is always the label.
	let { status }: { status: string } = $props();

	// Full literal Tailwind class strings: the JIT scanner only sees complete
	// class strings, so these must never be built dynamically (e.g. `bg-${c}-100`)
	// or they get purged and the badge ships unstyled.
	const STATUS_CLASSES: Record<string, string> = {
		todo: 'bg-gray-100 text-gray-800',
		'in-progress': 'bg-amber-100 text-amber-800',
		done: 'bg-green-100 text-green-800',
		blocked: 'bg-red-100 text-red-800'
	};
	const NEUTRAL_CLASSES = 'bg-slate-100 text-slate-700';
</script>

<span
	class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium {STATUS_CLASSES[
		status
	] ?? NEUTRAL_CLASSES}"
>
	{status}
</span>
