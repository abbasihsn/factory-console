<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import type { Core } from 'cytoscape';
	// Type-only default import so `cy.layout()` accepts dagre's `rankDir` (the
	// @types/cytoscape-dagre `DagreLayoutOptions` extends cytoscape's layout options
	// but isn't in its `LayoutOptions` union). Erased at build — the runtime library
	// is pulled in via the dynamic `import()` in onMount below.
	import type CytoscapeDagre from 'cytoscape-dagre';
	import type { RunState, TicketGraph } from '$lib/api';

	let { graph }: { graph: TicketGraph } = $props();

	// Both arrays are OPTIONAL in the generated schema (the backend may omit them),
	// so guard with `?? []`. The visually-hidden node list below and the cytoscape
	// elements are both built from these.
	const nodes = $derived(graph.nodes ?? []);
	const edges = $derived(graph.edges ?? []);

	// Cytoscape paints to an opaque <canvas> and only understands hex/named colors,
	// NOT Tailwind class names — so this mirrors RunStateBadge's palette INTENT
	// (todo=gray, in-flight=amber, ready=green, merged=violet, unknown=slate) as
	// concrete solid fills. Keep in sync with RunStateBadge's STATE_CLASSES.
	const RUN_STATE_HEX: Record<RunState, string> = {
		todo: '#9ca3af', // gray-400
		'in-flight': '#f59e0b', // amber-500
		ready: '#22c55e', // green-500
		merged: '#8b5cf6', // violet-500
		unknown: '#94a3b8' // slate-400
	};

	// The container the cytoscape canvas mounts into (client-only; see onMount).
	let container: HTMLDivElement | undefined;
	let cy: Core | undefined;

	// Cytoscape needs the DOM, so it can only initialize client-side. `onMount`
	// already runs client-only, and the cytoscape libraries are pulled in via
	// dynamic `import()` HERE (not top-level) so nothing evaluates during SSR and
	// the co-located test can `vi.mock` them cleanly.
	onMount(async () => {
		if (!container) return;

		const cytoscape = (await import('cytoscape')).default;
		const cytoscapeDagre = (await import('cytoscape-dagre')).default;
		// `dagre` is a peer of cytoscape-dagre; importing it here ensures Vite
		// bundles it into the static output (no CDN).
		await import('dagre');
		cytoscape.use(cytoscapeDagre);

		cy = cytoscape({
			container,
			elements: [
				...nodes.map((node) => ({
					data: { id: node.id, label: node.id, runState: node.runState }
				})),
				// An edge's `source` depends on its `target`; both ids are guaranteed to
				// resolve to nodes by the backend (dangling deps are never emitted).
				...edges.map((edge) => ({
					data: { id: `${edge.source}->${edge.target}`, source: edge.source, target: edge.target }
				}))
			],
			style: [
				{
					selector: 'node',
					style: {
						'background-color': (ele) => RUN_STATE_HEX[ele.data('runState') as RunState],
						label: 'data(label)',
						'font-size': '10px',
						color: '#1e293b', // slate-800
						'text-valign': 'center',
						'text-halign': 'center',
						width: 40,
						height: 40
					}
				},
				{
					selector: 'edge',
					style: {
						width: 1.5,
						'line-color': '#cbd5e1', // slate-300
						'target-arrow-color': '#cbd5e1',
						'target-arrow-shape': 'triangle',
						'curve-style': 'bezier'
					}
				}
			]
		});

		cy.layout({ name: 'dagre', rankDir: 'LR' } as CytoscapeDagre.DagreLayoutOptions).run();

		// Node tap routes client-side to that ticket's detail page.
		cy.on('tap', 'node', (evt) => {
			void goto(`/tickets/${evt.target.id()}`);
		});

		// Expose the core for e2e / manual introspection (Cytoscape's canvas is opaque
		// to the DOM; the accessible <nav> below is the primary e2e surface).
		(window as unknown as { __cy?: Core }).__cy = cy;
	});

	onDestroy(() => {
		cy?.destroy();
	});
</script>

<!-- Cytoscape renders its <canvas> into this element. -->
<div bind:this={container} class="h-[70vh] w-full" data-testid="dep-graph-canvas"></div>

<!--
	Accessible node-hook (T51 e2e contract, NOT optional): Cytoscape paints to an
	opaque <canvas>, so this visually-hidden list is the DOM surface e2e queries.
	One <a> per node — accessible name = ticket id, `data-run-state` = its run-state,
	href = its detail route.
-->
<nav class="sr-only" aria-label="Ticket dependency nodes">
	{#each nodes as node (node.id)}
		<a href="/tickets/{node.id}" data-run-state={node.runState}>{node.id}</a>
	{/each}
</nav>
