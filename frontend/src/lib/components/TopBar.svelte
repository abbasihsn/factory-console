<script lang="ts">
	import type { RegisteredProjectOut } from '$lib/api';
	import type { Project } from '$lib/api/contracts';
	import NavSearch from '$lib/components/NavSearch.svelte';
	import ProjectSwitcher from '$lib/components/ProjectSwitcher.svelte';

	// Presentational only: no `$app/*` imports, so it renders deterministically
	// under vitest/jsdom with supplied props. `NavSearch` encapsulates the header's
	// navigation (it owns the `goto`) and `ProjectSwitcher` the selection write,
	// keeping this component prop-only. The layout owns `invalidateAll`.
	//
	// The registry props default to "no registry", which is what the layout load
	// degrades to when it cannot be read — and what the switcher renders nothing
	// for, so a header given only a project looks exactly as it did before.
	let {
		project,
		projects = [],
		selectedId = null,
		onReload
	}: {
		project: Pick<Project, 'rootPath'>;
		projects?: readonly RegisteredProjectOut[];
		selectedId?: string | null;
		onReload?: () => void;
	} = $props();
</script>

<header class="border-b border-slate-200 bg-surface">
	<div class="mx-auto flex max-w-5xl items-center gap-4 px-4 py-3">
		<span class="shrink-0 font-semibold text-text">Factory Console</span>
		<!-- Beside the root path, which is the same fact this control changes: the
		     path names what is being served, the dropdown picks it. -->
		<ProjectSwitcher {projects} {selectedId} />
		<span class="min-w-0 flex-1 truncate font-mono text-sm text-muted" title={project.rootPath}>
			{project.rootPath}
		</span>
		<NavSearch />
		<button
			type="button"
			class="shrink-0 rounded border border-slate-300 px-3 py-1 text-sm text-text hover:bg-bg"
			onclick={() => onReload?.()}
		>
			Reload
		</button>
	</div>
</header>
