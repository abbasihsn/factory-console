<script lang="ts">
	import type { Project } from '$lib/api/contracts';

	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with supplied props. The layout owns `invalidateAll`.
	let { project, onReload }: { project: Pick<Project, 'rootPath'>; onReload?: () => void } =
		$props();
</script>

<header class="border-b border-slate-200 bg-surface">
	<div class="mx-auto flex max-w-5xl items-center gap-4 px-4 py-3">
		<span class="shrink-0 font-semibold text-text">Factory Console</span>
		<span class="min-w-0 flex-1 truncate font-mono text-sm text-muted" title={project.rootPath}>
			{project.rootPath}
		</span>
		<button
			type="button"
			class="shrink-0 rounded border border-slate-300 px-3 py-1 text-sm text-text hover:bg-bg"
			onclick={() => onReload?.()}
		>
			Reload
		</button>
	</div>
</header>
