<script lang="ts">
	import { untrack } from 'svelte';
	import MarkdownEditor from '$lib/components/MarkdownEditor.svelte';
	import { validateTicketForm, type TicketFormValues } from '$lib/forms/ticketForm';

	// Presentational only: no `$app/*` imports, no fetch — it renders
	// deterministically under vitest/jsdom with supplied props. The form emits the
	// collected `TicketFormValues` through `onSubmit` and NEVER calls the API, so
	// each caller (create route, detail-route edit flow) owns its own
	// write/confirm orchestration.
	//
	// Fields rendered are EXACTLY those in `TicketFormValues` (id, title, the
	// newline-list textareas dependsOn/files, and the single-line provides) plus the
	// markdown body.
	// The ticket's day-one prose mentioned `status`/`track`/`milestone`, but those
	// are NOT part of the `TicketFormValues` contract T67 shipped and have nowhere
	// to go in `onSubmit`, so they are intentionally absent.
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
	// hint). The list fields (`dependsOn`, `files`) stay as raw newline-delimited
	// strings here (as `TicketFormValues` holds them); the caller runs `parseList` on
	// THOSE TWO when it builds the API payload — `provides` is a scalar on the wire and
	// must be passed through as-is. `body` is optional on the type, so seed it from
	// `initial.body ?? ''`.
	let id = $state(untrack(() => initial.id));
	let title = $state(untrack(() => initial.title));
	let dependsOn = $state(untrack(() => initial.dependsOn));
	let provides = $state(untrack(() => initial.provides));
	let files = $state(untrack(() => initial.files));
	let body = $state(untrack(() => initial.body ?? ''));

	// The current form values, reassembled on every edit. This is what `onSubmit`
	// hands up — INCLUDING `body`.
	const currentValues = $derived<TicketFormValues>({
		id,
		title,
		dependsOn,
		provides,
		files,
		body
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

	<!-- dependsOn and files are newline-delimited lists; one entry per line (parsed by
	     the caller). provides, between them below, is deliberately NOT one. -->
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

	<label class="flex flex-col gap-1 text-xs text-muted">
		Files
		<textarea
			class={FIELD_CLASS}
			rows="3"
			aria-label="Files"
			placeholder="One file path per line"
			{disabled}
			bind:value={files}
		></textarea>
	</label>

	<div class="flex flex-col gap-1 text-xs text-muted">
		<span>Body</span>
		<!-- MarkdownEditor is a raw-markdown source editor; `disabled` makes it
		     read-only alongside the other inert fields. -->
		<MarkdownEditor
			value={body}
			onChange={(v) => (body = v)}
			readOnly={disabled}
			ariaLabel="Ticket body"
		/>
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
