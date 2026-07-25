# [T68] Reusable TicketForm component with live validation

milestone: v2 · track: frontend · depends_on: T29, T67 · provides: TicketForm.svelte — editable front-matter fields + MarkdownEditor body with live per-field validation, driving both create and edit.

## Context

Both the create route and the detail-route edit flow need the same form; centralizing it here keeps them consistent and PR-sized. TicketForm renders the editable front-matter fields plus the markdown body, runs live validation on every change (disabling submit while invalid and showing per-field errors), and is purely presentational — it emits the collected values and never calls the API, so it unit-tests cleanly and both callers own their own write/confirm orchestration.

## Staged approach

1. Add `src/lib/components/TicketForm.svelte` with props `{ mode: 'create' | 'edit'; initial: TicketFormValues; disabled?: boolean; onSubmit: (values: TicketFormValues) => void; onValidityChange?: (valid: boolean) => void }`.
2. Render inputs for `id` (read-only in edit mode since the PUT keeps the id fixed; editable + required in create), `title`, `status`, `track`, `milestone`, and newline-list textareas for `dependsOn`/`provides`/`files`, plus `<MarkdownEditor>` for the body — using the established Tailwind field styling.
3. Wire `$derived` validation via `validateTicketForm(values, { mode })`; show per-field error text; disable the submit button while invalid or `disabled`; call `onValidityChange` when validity flips.
4. On submit, hand the assembled `TicketFormValues` to `onSubmit` (no API call here).
5. Respect `disabled` (used by the non-todo gate) by making all fields + submit inert.

## Critical files

- `frontend/src/lib/components/TicketForm.svelte` (new)

## Interface & data

Props: `{ mode: 'create'|'edit'; initial: TicketFormValues; disabled?: boolean; onSubmit: (values: TicketFormValues) => void; onValidityChange?: (valid: boolean) => void }`. Consumes `TicketFormValues`/`validateTicketForm` and `MarkdownEditor` from T67. By reference: the `TicketCreate`/`TicketUpdate` field set (T66 regenerated types) determines which fields are present; `id` immutable on edit per the PUT contract. No DB. NFR: live client-side validation mirrors server rules (not the sole gate); `disabled` prop enforces the non-todo gate at the field level.

## Verification

Vitest `TicketForm.test.ts`: renders both modes; typing an invalid id disables submit and shows the error; a valid create/edit fires `onSubmit` with the expected `TicketFormValues`; `disabled` makes fields inert; `id` is read-only in edit mode. `pnpm check`, `pnpm test`, `pnpm lint` green.
