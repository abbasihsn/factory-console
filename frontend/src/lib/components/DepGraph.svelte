<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import type { Core } from 'cytoscape';
	// Type-only default import so `cy.layout()` accepts dagre's `rankDir` (the
	// @types/cytoscape-dagre `DagreLayoutOptions` extends cytoscape's layout options
	// but isn't in its `LayoutOptions` union). Erased at build — the runtime library
	// is pulled in via the dynamic `import()` in onMount below.
	import type CytoscapeDagre from 'cytoscape-dagre';
	// The cytoscape factory's TYPE, for the `$state` that holds it between the
	// dynamic import resolving and the $effect painting with it. `cytoscape` is an
	// `export =` module, so its type-only default import IS the callable's type.
	import type CytoscapeFactory from 'cytoscape';
	import type { RunState, TicketGraph } from '$lib/api';

	let { graph }: { graph: TicketGraph } = $props();

	// Both arrays are OPTIONAL in the generated schema (the backend may omit them),
	// so guard with `?? []`. The visually-hidden node list below and the cytoscape
	// elements are both built from these.
	const nodes = $derived(graph.nodes ?? []);
	const edges = $derived(graph.edges ?? []);

	// Cytoscape paints to an opaque <canvas> and only understands hex/named colors,
	// NOT Tailwind class names — so this mirrors RunStateBadge's palette INTENT
	// (todo=gray, in-progress=amber, ready=green, merged=violet, the failure-ish
	// states=red, and the two no-lane-state answers unknown/absent=slate, in
	// adjacent shades) as concrete solid fills. Keep in sync with RunStateBadge's
	// STATE_CLASSES.
	const RUN_STATE_HEX: Record<RunState, string> = {
		todo: '#9ca3af', // gray-400
		'in-flight': '#f59e0b', // amber-500
		in_progress: '#f59e0b', // amber-500
		in_part: '#fbbf24', // amber-400
		in_submilestone: '#fbbf24', // amber-400
		ready: '#22c55e', // green-500
		merged: '#8b5cf6', // violet-500
		flagged: '#ef4444', // red-500
		failed: '#b91c1c', // red-700
		needs_human: '#dc2626', // red-600
		unknown: '#94a3b8', // slate-400
		absent: '#64748b', // slate-500
		// Red, not slate: unlike `unknown`/`absent` (a source that answered), this is a
		// source the console could not read, and every write to the node is refused.
		// Distinct from `flagged`/`failed`/`needs_human` so a graph can still tell a
		// lane failure from a console-side one.
		unreadable: '#fb7185' // rose-400
	};

	// The container the cytoscape canvas mounts into (client-only; see onMount).
	let container: HTMLDivElement | undefined;
	let cy: Core | undefined;

	// The cytoscape constructor, once its chunk has loaded. `$state` so the $effect
	// below re-runs and paints as soon as it arrives.
	let cytoscapeLib = $state<typeof CytoscapeFactory | undefined>(undefined);

	// Latched by onDestroy. `onMount`'s body is async and Svelte does NOT cancel it,
	// so without this a component unmounted DURING the four dynamic imports would
	// still resolve them and build a full core into a detached container — with a
	// live tap handler that can still `goto()`, and a `window.__cy` nothing will ever
	// destroy.
	let destroyed = false;

	// Cytoscape needs the DOM, so it can only load client-side. `onMount` already
	// runs client-only, and the libraries are pulled in via dynamic `import()` HERE
	// (not top-level) so nothing evaluates during SSR and the co-located test can
	// `vi.mock` them cleanly. This only LOADS; painting is the $effect's job, so a
	// later `graph` never finds the canvas frozen at its first value.
	onMount(async () => {
		const cytoscape = (await import('cytoscape')).default;
		const cytoscapeDagre = (await import('cytoscape-dagre')).default;
		// `dagre` is a peer of cytoscape-dagre; importing it here ensures Vite
		// bundles it into the static output (no CDN).
		await import('dagre');
		cytoscape.use(cytoscapeDagre);
		if (destroyed) return;
		cytoscapeLib = cytoscape;
	});

	/** Destroy the live core and drop the debug handle. Idempotent. */
	function teardown() {
		cy?.destroy();
		cy = undefined;
		const debugWindow = window as unknown as { __cy?: Core };
		// Cleared, not just reassigned on the next build: a destroyed core left on
		// `window` is pinned against GC for the life of the page, so every visit to
		// /graph would retain the previous one.
		delete debugWindow.__cy;
	}

	// Rebuild the canvas whenever the graph changes. The root layout `invalidateAll()`s
	// on every SSE bump, so `graph` is replaced whenever the factory touches run-state
	// or the manifest. Building once in `onMount` left the painted DAG frozen at its
	// first value while the `$derived` <nav> below kept updating — the same page
	// showing two different graphs, and a ticket that moved todo -> merged (or a
	// newly created one) never appearing until a hard reload.
	$effect(() => {
		const cytoscape = cytoscapeLib;
		// Read both so this effect re-runs when either changes.
		const currentNodes = nodes;
		const currentEdges = edges;
		if (!cytoscape || !container) return;

		const core = cytoscape({
			container,
			elements: [
				...currentNodes.map((node) => ({
					data: { id: node.id, label: node.id, runState: node.runState }
				})),
				// An edge's `source` depends on its `target`; both ids are guaranteed to
				// resolve to nodes by the backend (dangling deps are never emitted).
				...currentEdges.map((edge) => ({
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

		core.layout({ name: 'dagre', rankDir: 'LR' } as CytoscapeDagre.DagreLayoutOptions).run();

		// Node tap routes client-side to that ticket's detail page.
		core.on('tap', 'node', (evt) => {
			void goto(`/tickets/${evt.target.id()}`);
		});

		cy = core;
		// Expose the core for e2e / manual introspection (Cytoscape's canvas is opaque
		// to the DOM; the accessible <nav> below is the primary e2e surface).
		(window as unknown as { __cy?: Core }).__cy = core;

		// Runs before each REBUILD as well as on destroy, so a graph update replaces
		// the core instead of stacking a second one on the same container.
		return teardown;
	});

	onDestroy(() => {
		destroyed = true;
		teardown();
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
