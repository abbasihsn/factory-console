# [T60] FileWriter Protocol + FakeFileWriter (in-memory)

milestone: v2 · track: file-adapter · depends_on: T10, T55, T57, T58 · provides: file_adapter/writer_protocol.py (FileWriter port symmetric to FileAdapter) + fake_writer.py (in-memory FakeFileWriter for tests).

## Context

The write seam the backend track's POST/PUT/DELETE endpoints depend on via `FastAPI.Depends()` — mirroring how read handlers depend on the `FileAdapter` Protocol and never touch `open()` directly. Defining the port plus an in-memory `FakeFileWriter` lets the backend and its integration tests be built against a side-effect-free implementation, exactly as `FakeFileAdapter` enabled the read side.

## Staged approach

1. CREATE `server/factory_console/file_adapter/writer_protocol.py`: a `@runtime_checkable FileWriter(Protocol)` with `preview_create(project, draft: TicketDraft) -> DiffPreview`, `create_ticket(project, draft) -> WriteResult`, `preview_edit(project, ticket_id, edit: TicketEdit) -> DiffPreview`, `edit_ticket(project, ticket_id, edit) -> WriteResult`, `preview_delete(project, ticket_id) -> DiffPreview`, `delete_ticket(project, ticket_id) -> WriteResult`. Docstring: the port is the write mirror of `FileAdapter`; apply methods enforce the mutability gate, previews are pure diffs.
2. CREATE `server/factory_console/file_adapter/fake_writer.py`: `FakeFileWriter` seeded with an in-memory manifest (list of entry dicts), a `{id: bodyMarkdown}` map, an optional roadmap body, and a `{id: RunState}` map. Reuse `write_render` (T57) against an in-memory Project-like view where possible, or replicate the same create/edit/delete semantics over the seeded dicts; apply methods enforce todo-only mutability by consulting the seeded run-state map (raise `TicketNotMutable` for non-todo on edit/delete); `preview_*` return `DiffPreview` via the T58 diff engine over seeded-vs-proposed text; mutations update the in-memory state and return `WriteResult`.
3. `isinstance(fake, FileWriter)` holds (runtime_checkable).
4. Import by full path; do NOT edit `file_adapter/__init__.py`.

## Critical files

- `server/factory_console/file_adapter/writer_protocol.py` (new)
- `server/factory_console/file_adapter/fake_writer.py` (new)
- `tests/unit/test_fake_writer.py` (new)

## Interface & data

Port methods (inputs/outputs) as listed — `DiffPreview` for `preview_*`, `WriteResult` for apply. By reference: `TicketDraft`/`TicketEdit`/`DiffPreview`/`WriteResult` (T55); mirrors the `FileAdapter` Protocol shape (T10) and is wired via `FastAPI.Depends()` by the backend (like `get_file_adapter`); reuses the diff engine (T58) and write-render semantics (T57); the `TicketNotMutable` gate semantics (T56 concept). In-memory (no DB). NFR: `@runtime_checkable` structural conformance; apply methods enforce todo-only mutability; previews side-effect-free.

## Verification

`pytest tests/unit/test_fake_writer.py`: `isinstance(FakeFileWriter(...), FileWriter)`; create adds an in-memory ticket and returns `WriteResult.changedFiles=[3 paths]`; edit on a seeded todo ticket updates it; edit/delete on a seeded in-flight ticket raises `TicketNotMutable`; `preview_edit` returns a `DiffPreview` whose files carry unified diffs; no attribute/module touches the filesystem.
