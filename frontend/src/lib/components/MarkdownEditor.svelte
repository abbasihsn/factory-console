<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { basicSetup } from 'codemirror';
	import { EditorView } from '@codemirror/view';
	import { Annotation, Compartment, EditorState } from '@codemirror/state';
	import { markdown } from '@codemirror/lang-markdown';

	// RAW-TEXT editor, NOT a renderer. CodeMirror only syntax-highlights the
	// markdown SOURCE; it never turns markdown into HTML. This does NOT violate the
	// repo's "Never render markdown client-side" rule — that rule is about the
	// `{@html}` render path (see MarkdownBody.svelte), which this component avoids
	// entirely. Do NOT add a markdown->HTML preview here.
	let {
		value,
		onChange,
		readOnly = false,
		ariaLabel
	}: {
		value: string;
		onChange: (v: string) => void;
		readOnly?: boolean;
		ariaLabel?: string;
	} = $props();

	// The element CodeMirror mounts its editable surface into (client-only).
	let container: HTMLDivElement | undefined;
	let view: EditorView | undefined;

	// A Compartment lets `readOnly` be reconfigured in place when the prop changes,
	// without tearing down and rebuilding the whole EditorView.
	const readOnlyCompartment = new Compartment();

	// Tags transactions produced by the external-`value` reconcile below so the
	// updateListener can tell them apart from genuine user edits. `onChange` is a
	// USER-INPUT signal, so a programmatic reconcile (form reset, async load,
	// switching tickets) must NOT fire it — otherwise a parent using `onChange` as
	// a dirty/"user modified" signal is wrongly tripped by its own value push.
	const External = Annotation.define<boolean>();

	// readOnly needs BOTH: `EditorState.readOnly` blocks transactions from user
	// input, and `EditorView.editable.of(false)` makes the contentEditable surface
	// genuinely non-editable (renders `contenteditable="false"`).
	function readOnlyExtension(ro: boolean) {
		return [EditorState.readOnly.of(ro), EditorView.editable.of(!ro)];
	}

	// CodeMirror needs the DOM, so it can only initialize client-side; `onMount`
	// runs client-only.
	onMount(() => {
		if (!container) return;

		const extensions = [
			basicSetup,
			markdown(),
			// Surface USER-originated doc changes to the parent. Transactions tagged
			// `External` come from the value-reconcile $effect below (not a user edit),
			// so we skip them — `onChange` fires only for edits the user actually made.
			EditorView.updateListener.of((update) => {
				if (update.docChanged && !update.transactions.some((t) => t.annotation(External))) {
					onChange(update.state.doc.toString());
				}
			}),
			readOnlyCompartment.of(readOnlyExtension(readOnly))
		];

		// Land the aria-label on the `[contenteditable]` node itself so assistive
		// tech names the actual editing surface.
		if (ariaLabel) {
			extensions.push(EditorView.contentAttributes.of({ 'aria-label': ariaLabel }));
		}

		view = new EditorView({
			state: EditorState.create({ doc: value, extensions }),
			parent: container
		});

		// Test/e2e introspection hook (mirrors DepGraph's `window.__cy`): dispatching
		// a change transaction against this view is the deterministic way to drive an
		// edit under jsdom, where synthetic keystrokes into contentEditable are flaky.
		(container as HTMLDivElement & { __view?: EditorView }).__view = view;
	});

	onDestroy(() => {
		view?.destroy();
	});

	// Reconcile EXTERNAL `value` changes (e.g. a form reset) into the doc. Two guards:
	// (1) when the change originated from user typing, `onChange` already pushed the
	// same text into `value`, so the doc already matches and we skip the dispatch —
	// no echo transaction; (2) a genuine external change IS dispatched, but tagged
	// `External` so the updateListener does NOT re-surface it through `onChange` (a
	// programmatic reconcile is not a user edit).
	$effect(() => {
		const next = value;
		if (view && next !== view.state.doc.toString()) {
			view.dispatch({
				changes: { from: 0, to: view.state.doc.length, insert: next },
				annotations: External.of(true)
			});
		}
	});

	// Reconcile `readOnly` changes through the compartment.
	$effect(() => {
		const ro = readOnly;
		view?.dispatch({ effects: readOnlyCompartment.reconfigure(readOnlyExtension(ro)) });
	});
</script>

<div bind:this={container} class="cm-host" data-testid="markdown-editor"></div>
