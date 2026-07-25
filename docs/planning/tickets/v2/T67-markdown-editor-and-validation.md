# [T67] CodeMirror markdown editor + ticket-form validation & editability modules

milestone: v2 · track: frontend · depends_on: T28, T29 · provides: MarkdownEditor.svelte (CodeMirror 6) + lib/forms/ticketForm.ts (TICKET_ID_PATTERN + required-field validation) + lib/forms/editability.ts (isEditable todo/unknown predicate).

## Context

The edit and create flows share one body editor and one validation contract. This ticket adds the reusable CodeMirror 6 markdown editor plus the PURE, unit-testable form logic — the client-side mirror of the server's `TICKET_ID_PATTERN` and required-field rules, and the `isEditable(runState)` predicate that mirrors the server `RunStateGate`. Keeping validation/editability as pure functions (no Svelte, no I/O) lets them be exhaustively tested and reused by TicketForm, the detail wiring, and the create route.

**Editor decision (load-bearing): CodeMirror 6, not Monaco** — modular/tree-shakeable ESM that bundles cleanly under Vite + adapter-static with no separate web-worker/`MonacoEnvironment` plumbing to embed in the single wheel; we only need a lightweight markdown surface. Monaco is multi-MB and its worker setup fights the single-wheel packaging.

## Staged approach

1. Add CodeMirror deps to `package.json` devDependencies (matching the existing pattern where bundled libs like cytoscape live in devDependencies): `codemirror`, `@codemirror/state`, `@codemirror/view`, `@codemirror/commands`, `@codemirror/lang-markdown`.
2. Add `src/lib/components/MarkdownEditor.svelte`: props `{ value: string; onChange: (v: string) => void; readOnly?: boolean; ariaLabel?: string }`; construct an `EditorView` in `onMount` with basic setup + markdown language + an update listener calling `onChange`; reconcile external `value` changes; destroy in `onDestroy`; a `readOnly` compartment/extension for the disabled state.
3. Add `src/lib/forms/ticketForm.ts`: export `TICKET_ID_PATTERN = /^[A-Za-z0-9_.-]+$/` (a MIRROR of `factory_console.domain.ticket.TICKET_ID_PATTERN` — cite the source of truth in a comment), the `TicketFormValues`/`TicketFormErrors` types, list-field parse/serialize helpers (dependsOn/provides/files as newline lists), and `validateTicketForm(values, { mode: 'create' | 'edit' }): TicketFormErrors`.
4. Add `src/lib/forms/editability.ts`: `isEditable(runState: RunState): boolean` returning true only for `todo`/`unknown` (cite the run-state editing gate).

## Critical files

- `frontend/package.json`
- `frontend/src/lib/components/MarkdownEditor.svelte` (new)
- `frontend/src/lib/forms/ticketForm.ts` (new)
- `frontend/src/lib/forms/editability.ts` (new)

## Interface & data

MarkdownEditor props `{ value, onChange, readOnly?, ariaLabel? }`. `validateTicketForm(values: TicketFormValues, opts: { mode: 'create'|'edit' }): TicketFormErrors` — required: `title` always, `id` in create mode; `id` must match `TICKET_ID_PATTERN`; returns a per-field error map (empty = valid). `isEditable(runState: RunState): boolean`. By reference: `TICKET_ID_PATTERN` (ARCHITECTURE "Ticket-id constraint", source `domain/ticket.py`) mirrored client-side; `RunState` enum + run-state editing gate mirrored via `isEditable`; the write payload field set aligns with `TicketCreate`/`TicketUpdate` from T66's regenerated types. No DB. NFR: input validation is a defense-in-depth MIRROR of the server, never the sole gate.

## Verification

`pnpm check` passes (TS-strict). Vitest specs: `ticketForm.test.ts` covers valid/invalid ids (spaces, slashes, empty), required-field enforcement per mode, and list parse/serialize round-trips; `editability.test.ts` covers all five RunState values; `MarkdownEditor.test.ts` mounts under jsdom, asserts initial value and that typing fires `onChange` and `readOnly` blocks edits. `pnpm test` + `pnpm lint` green; `pnpm build` succeeds (CodeMirror bundles under adapter-static).
