<script lang="ts">
	import { untrack } from 'svelte';
	import { validateTicketForm, type TicketFormValues } from '$lib/forms/ticketForm';

	// Presentational only: no `$app/*` imports, no fetch — it renders
	// deterministically under vitest/jsdom with supplied props. The form emits the
	// collected `TicketFormValues` through `onSubmit` and NEVER calls the API, so
	// each caller (create route, detail-route edit flow) owns its own
	// write/confirm orchestration.
	//
	// Fields rendered are EXACTLY those in `TicketFormValues`, and they now come in
	// two groups because App Factory v3 stores them in two files. The INDEX fields
	// (id, title, dependsOn, provides) land in `tickets.json`; the five CONTENT
	// fields land in the ticket's own JSON document. Nothing is written to both,
	// which is what keeps the two from disagreeing.
	//
	// THE SINGLE BODY TEXTAREA IS GONE. It was not moved or renamed — a v3 ticket
	// has no free-text body, and `schemas/ticket.schema.json` sets
	// `additionalProperties: false`, so there is nowhere to put a paragraph that
	// belongs to no field. Offering one would collect prose the server must then
	// refuse or silently drop.
	//
	// The ticket's day-one prose mentioned `status`/`track`/`milestone`, but those
	// are NOT part of the `TicketFormValues` contract and have nowhere to go in
	// `onSubmit`, so they are intentionally absent.
	let {
		mode,
		initial,
		disabled = false,
		onSubmit,
		onValidityChange
	}: {
		mode: 'create' | 'edit';
		initial: TicketFormValues;
		disabled?: boolean;
		onSubmit: (values: TicketFormValues) => void;
		onValidityChange?: (valid: boolean) => void;
	} = $props();

	// Local editable state seeded ONCE from `initial` — later prop changes must not
	// clobber in-progress edits, so `untrack` documents that we read only the
	// initial snapshot (this also silences Svelte's `state_referenced_locally`
	// hint). The list fields (`dependsOn`, `criticalFiles`, `verificationCommands`)
	// stay as raw newline-delimited strings here, as `TicketFormValues` holds them;
	// the caller runs `parseList` on THOSE THREE when it builds the API payload —
	// `provides` is a scalar on the wire and must be passed through as-is.
	let id = $state(untrack(() => initial.id));
	let title = $state(untrack(() => initial.title));
	let dependsOn = $state(untrack(() => initial.dependsOn));
	let provides = $state(untrack(() => initial.provides));
	let context = $state(untrack(() => initial.context));
	let approach = $state(untrack(() => initial.approach));
	let criticalFiles = $state(untrack(() => initial.criticalFiles));
	let interfaceData = $state(untrack(() => initial.interfaceData));
	let verificationCommands = $state(untrack(() => initial.verificationCommands));
	let verificationNotes = $state(untrack(() => initial.verificationNotes ?? ''));

	// The current form values, reassembled on every edit. This is what `onSubmit`
	// hands up.
	const currentValues = $derived<TicketFormValues>({
		id,
		title,
		dependsOn,
		provides,
		context,
		approach,
		criticalFiles,
		interfaceData,
		verificationCommands,
		verificationNotes
	});

	// Live client-side validation, mirroring the server rules (defense in depth,
	// never the sole gate). An empty error map means the form is valid.
	const errors = $derived(validateTicketForm(currentValues, { mode }));
	const isValid = $derived(Object.keys(errors).length === 0);

	// The submit button is inert while the form is invalid OR the parent has
	// disabled the whole form (the non-todo edit gate).
	const submitDisabled = $derived(disabled || !isValid);

	// Notify the parent only when validity FLIPS, not on every render. `prevValid`
	// is a plain (non-reactive) variable so writing it inside the effect does NOT
	// retrigger the effect — that keeps this loop-free. Starting it `undefined`
	// means the first run always differs from the initial validity, so the parent
	// also learns the starting state once on mount.
	let prevValid: boolean | undefined = undefined;
	$effect(() => {
		const valid = isValid;
		if (valid !== prevValid) {
			prevValid = valid;
			onValidityChange?.(valid);
		}
	});

	function handleSubmit(event: SubmitEvent): void {
		event.preventDefault();
		// Guard: never emit while invalid or disabled (the button is also disabled,
		// but a stray Enter/submit must not slip through).
		if (submitDisabled) return;
		onSubmit(currentValues);
	}

	// Shared field styling, mirroring FiltersBar's control idiom.
	const FIELD_CLASS =
		'rounded border border-slate-300 bg-surface px-2 py-1 text-sm text-text disabled:cursor-not-allowed disabled:opacity-60 read-only:bg-bg read-only:text-muted';
	const SECTION_CLASS = 'flex flex-col gap-4 border-t border-slate-200 pt-4';
</script>

<form class="flex flex-col gap-4" onsubmit={handleSubmit}>
	<!-- id: editable + required in create; read-only in edit (the PUT keeps id fixed). -->
	<label class="flex flex-col gap-1 text-xs text-muted">
		Ticket id
		<input
			type="text"
			class={FIELD_CLASS}
			aria-label="Ticket id"
			aria-invalid={errors.id ? 'true' : undefined}
			aria-describedby={errors.id ? 'ticket-form-id-error' : undefined}
			readonly={mode === 'edit'}
			{disabled}
			bind:value={id}
		/>
		{#if errors.id}
			<span id="ticket-form-id-error" class="text-xs text-danger">{errors.id}</span>
		{/if}
	</label>

	<label class="flex flex-col gap-1 text-xs text-muted">
		Title
		<input
			type="text"
			class={FIELD_CLASS}
			aria-label="Title"
			aria-invalid={errors.title ? 'true' : undefined}
			aria-describedby={errors.title ? 'ticket-form-title-error' : undefined}
			{disabled}
			bind:value={title}
		/>
		{#if errors.title}
			<span id="ticket-form-title-error" class="text-xs text-danger">{errors.title}</span>
		{/if}
	</label>

	<!-- dependsOn is a newline-delimited list; one entry per line (parsed by the
	     caller). provides, below it, is deliberately NOT one. -->
	<label class="flex flex-col gap-1 text-xs text-muted">
		Depends on
		<textarea
			class={FIELD_CLASS}
			rows="3"
			aria-label="Depends on"
			placeholder="One ticket id per line"
			{disabled}
			bind:value={dependsOn}
		></textarea>
	</label>

	<!-- provides is a SCALAR on the wire (`TicketDraft.provides: string`), so it gets a
	     single-line input, not a newline list — see the contract note in
	     `$lib/forms/ticketForm.ts`. A textarea here would invite a multi-entry value
	     that the server stores verbatim and the read model hands back collapsed into
	     one element. -->
	<label class="flex flex-col gap-1 text-xs text-muted">
		Provides
		<input
			type="text"
			class={FIELD_CLASS}
			aria-label="Provides"
			placeholder="Capability this ticket provides"
			{disabled}
			bind:value={provides}
		/>
	</label>

	<!-- The five CONTENT fields. Plain textareas rather than the MarkdownEditor the
	     single body used: these are five short fields, and five CodeMirror instances
	     in one modal buys syntax highlighting for prose that is rendered into fixed
	     `## ` sections whose structure the user does not write. -->
	<div class={SECTION_CLASS}>
		<label class="flex flex-col gap-1 text-xs text-muted">
			Context
			<textarea
				class={FIELD_CLASS}
				rows="4"
				aria-label="Context"
				aria-invalid={errors.context ? 'true' : undefined}
				placeholder="Why this ticket exists, what it delivers, how it fits the sub-version"
				{disabled}
				bind:value={context}
			></textarea>
			{#if errors.context}
				<span class="text-xs text-danger">{errors.context}</span>
			{/if}
		</label>

		<label class="flex flex-col gap-1 text-xs text-muted">
			Staged approach
			<textarea
				class={FIELD_CLASS}
				rows="6"
				aria-label="Staged approach"
				aria-invalid={errors.approach ? 'true' : undefined}
				placeholder="The ordered build steps — the files to create or modify, in order"
				{disabled}
				bind:value={approach}
			></textarea>
			{#if errors.approach}
				<span class="text-xs text-danger">{errors.approach}</span>
			{/if}
		</label>

		<!-- criticalFiles is the one content field the factory acts on MECHANICALLY: it
		     feeds the overlap filter that serializes two lanes which would otherwise
		     edit the same path off bases lacking each other's changes. A short list
		     does not fail loudly, it silently weakens a concurrency guard — which is
		     why the hint says what the list is FOR rather than what shape it takes. -->
		<label class="flex flex-col gap-1 text-xs text-muted">
			Critical files
			<textarea
				class={FIELD_CLASS}
				rows="4"
				aria-label="Critical files"
				aria-invalid={errors.criticalFiles ? 'true' : undefined}
				placeholder="One path per line — every file this ticket creates or modifies"
				{disabled}
				bind:value={criticalFiles}
			></textarea>
			{#if errors.criticalFiles}
				<span class="text-xs text-danger">{errors.criticalFiles}</span>
			{/if}
		</label>

		<label class="flex flex-col gap-1 text-xs text-muted">
			Interface &amp; data
			<textarea
				class={FIELD_CLASS}
				rows="4"
				aria-label="Interface and data"
				aria-invalid={errors.interfaceData ? 'true' : undefined}
				placeholder="Inputs/outputs, contracts, entities touched — or N/A"
				{disabled}
				bind:value={interfaceData}
			></textarea>
			{#if errors.interfaceData}
				<span class="text-xs text-danger">{errors.interfaceData}</span>
			{/if}
		</label>

		<label class="flex flex-col gap-1 text-xs text-muted">
			Verification commands
			<textarea
				class={FIELD_CLASS}
				rows="3"
				aria-label="Verification commands"
				aria-invalid={errors.verificationCommands ? 'true' : undefined}
				placeholder="One shell command per line, run from the repo root"
				{disabled}
				bind:value={verificationCommands}
			></textarea>
			{#if errors.verificationCommands}
				<span class="text-xs text-danger">{errors.verificationCommands}</span>
			{/if}
		</label>

		<!-- The ONE optional content field, matching the schema. No error span: there is
		     no rule it can break. -->
		<label class="flex flex-col gap-1 text-xs text-muted">
			Verification notes (optional)
			<textarea
				class={FIELD_CLASS}
				rows="2"
				aria-label="Verification notes"
				placeholder="Context the commands need but cannot express — an env var, a service"
				{disabled}
				bind:value={verificationNotes}
			></textarea>
		</label>
	</div>

	<div>
		<button
			type="submit"
			class="rounded border border-slate-300 px-3 py-1 text-sm text-text hover:bg-bg disabled:cursor-not-allowed disabled:opacity-60"
			disabled={submitDisabled}
		>
			{mode === 'create' ? 'Create ticket' : 'Save changes'}
		</button>
	</div>
</form>
