# [T61] RealFileWriter — disk-backed FileWriter composing gate+render+diff+atomic

milestone: v2 · track: file-adapter · depends_on: T17, T56, T57, T58, T59, T60 · provides: file_adapter/real_writer.py — the production RealFileWriter the CLI/backend wire up; todo-gated, atomic, dry-run-capable create/edit/delete over real project files.

## Context

The production write adapter, mirroring `RealFileAdapter`: stateless, composed from the four single-purpose modules rather than re-implementing any of them. This is what `create_app`/`cli` wire behind the `FileWriter` `Depends()` so the backend's POST/PUT/DELETE endpoints get real, safe, atomic writes that refuse non-todo tickets and never touch the run-state directory.

## Staged approach

1. CREATE `server/factory_console/file_adapter/real_writer.py` with `RealFileWriter` (no constructor args, stateless, satisfies `FileWriter` structurally).
2. `preview_create` → `write_diff.preview('<id>', write_render.render_create(project, draft))`; `preview_edit`/`preview_delete` analogously over `render_edit`/`render_delete` — pure, no gate (the UI's disabled-state is the UX guard; previews stay side-effect-free).
3. `create_ticket(project, draft)`: `render_create` then `atomic_write.apply_changes`; return `WriteResult(applied=true, ticketId, changedFiles, diff, ticket=<re-read>)`.
4. `edit_ticket(project, ticket_id, edit)`: FIRST `write_gate.ensure_mutable(project, ticket_id)` (hard 409 `TicketNotMutable` for in-flight/ready/merged), THEN `render_edit` + `apply_changes`.
5. `delete_ticket(project, ticket_id)`: `ensure_mutable`, then `render_delete` + `apply_changes`.
6. Every write goes through `atomic_write` (which independently refuses run-state paths) — `RealFileWriter` never opens a file itself.
7. `isinstance(RealFileWriter(), FileWriter)` holds. Import by full path; do NOT edit `file_adapter/__init__.py`.

## Critical files

- `server/factory_console/file_adapter/real_writer.py` (new)
- `tests/unit/test_real_writer.py` (new)
- `tests/integration/test_real_writer_roundtrip.py` (new)

## Interface & data

Implements the `FileWriter` Protocol (T60) — `preview_*`/`create`/`edit`/`delete` signatures. Composition by reference: `RunStateGate.ensure_mutable` (T56, todo-only invariant), `write_render` (T57), `write_diff` (T58), `atomic_write.apply_changes` (T59); mirrors the stateless `RealFileAdapter` composition (T17). No DB. NFR: apply methods enforce the todo-only gate (409 `TicketNotMutable`); atomic all-or-nothing writes; MUST NOT write the run-state dir (guaranteed by the atomic layer + render); single-worker Uvicorn (no locks).

## Verification

`pytest tests/unit/test_real_writer.py` + `tests/integration/test_real_writer_roundtrip.py` against a tmp copy of `tests/fixtures/projects/with_run_state`: `create_ticket` writes all three files and the existing `RealFileAdapter` then lists/reads the new ticket; `edit_ticket` on a todo ticket mutates and re-reads correctly; `edit_ticket`/`delete_ticket` on CAD-125 (in-flight)/CAD-118 (ready)/CAD-100 (merged) raise `TicketNotMutable` and leave every file byte-identical; `preview_edit` returns a `DiffPreview` without mutating disk; property-based: a random sequence of edit/delete attempts NEVER mutates any non-todo ticket's files, and the `.factory/run-state` tree is byte-identical before and after every operation. `isinstance(RealFileWriter(), FileWriter)`.
