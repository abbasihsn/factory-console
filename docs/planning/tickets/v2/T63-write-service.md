# [T63] WriteService orchestrating FileWriter + RunStateGate + dry-run diff, with write error types

milestone: v2 · track: backend · depends_on: T22, T24, T55, T56, T58, T60 · provides: services/write_service.py WriteService (create/edit/delete/preview) + WriteConflict/WriteValidationError.

## Context

The write analogue of `TicketService`: a thin, per-request orchestrator that holds all write request logic so the handlers stay wiring-only. It rejects id collisions on create, computes the unified diff for a dry-run, and otherwise commits through the `FileWriter`. The non-todo editing gate is enforced INSIDE the writer via `RunStateGate` (T56) — its `TicketNotMutable` (`ticket_not_mutable`, 409) propagates unchanged, so WriteService does NOT define a second error for that condition. WriteService owns only the create-collision and validation error subclasses, co-located per the `errors.py` convention so the ONE existing exception handler renders them with no handler change.

## Staged approach

1. CREATE `server/factory_console/services/write_service.py`.
2. Co-located errors: `WriteConflict` (409, `write_conflict`), `WriteValidationError` (422, `write_validation_error`); reuse `TicketService.TicketNotFound` for absent ids on edit/delete. The non-todo 409 is NOT redefined here — the gate's `TicketNotMutable` (`ticket_not_mutable`, T56) propagates unchanged through WriteService (write_render's `TicketAlreadyExists` is an unreachable backstop behind `WriteConflict`).
3. Define `WriteService(writer: FileWriter, adapter: FileAdapter)`.
4. Per-verb helpers: `create(project, payload, *, dry_run)`, `edit(project, ticket_id, payload, *, dry_run)`, `delete(project, ticket_id, *, dry_run)`.
5. For create, raise `WriteConflict` if `adapter.get_ticket(project, id)` is not None; editability for edit/delete is handled by the writer's own gate. When `dry_run`, return the writer's `preview_*` (DiffPreview) wrapped in `WriteResult(applied=false)`; else call the writer's apply → `WriteResult(applied=true)` and re-read the resulting `Ticket` via `adapter.get_ticket`.
6. Do not touch the run-state directory. No `services/__init__` re-export.

## Critical files

- `server/factory_console/services/write_service.py` (new)

## Interface & data

`create/edit/delete(project, [ticket_id], payload?, *, dry_run) -> WriteResult` (T55 uniform envelope). By reference: write-core `FileWriter` port (T60) + `RunStateGate` (T56, gate error propagates); read `FileAdapter` (`get_ticket`, `read_run_state`); `RunState` enum; `TicketDraft`/`TicketEdit`/`WriteResult` (T55); `FactoryConsoleError`/`to_error_response` envelope; `TICKET_ID_PATTERN`. No DB. NFR: run-state editing gate (409) enforced in the writer; MUST NOT write run-state dir; single-worker so no locks.

## Verification

`pytest tests/unit` against a `FakeFileWriter` + `FakeFileAdapter`: edit/delete on an in-flight/ready/merged fixture raise `TicketNotMutable` (`ticket_not_mutable`, 409) and write nothing; create on an existing id raises `WriteConflict` (`write_conflict`); `dry_run=True` returns a diff and commits nothing; `dry_run=False` on a todo/unknown ticket commits and the re-read `Ticket` reflects the change. ruff clean.
