# [T57] Write-render — compute desired manifest+markdown+roadmap contents for a change

milestone: v2 · track: file-adapter · depends_on: T12, T13, T38, T55 · provides: file_adapter/write_render.py — pure functions turning a validated create/edit/delete into the desired full text of the three coupled files (a PlannedChange set), preserving unknown manifest fields.

## Context

The heart of the co-writer: given the project's CURRENT on-disk state and a `TicketDraft`/`TicketEdit` (or a delete id), compute exactly what each of `docs/planning/tickets.json`, the ticket `<id>.md`, and `ROADMAP.md` should contain — WITHOUT writing. Kept pure so the dry-run diff engine and the atomic co-writer consume the identical planned change-set (they can never disagree about what would change). Merges onto the existing manifest entry so unknown fields (e.g. `estimate`) survive — the same forward-compat tolerance the read side keeps on `Ticket.raw`.

## Staged approach

1. CREATE `server/factory_console/file_adapter/write_render.py`.
2. Frozen `PlannedChange(path: Path, relPath: str, currentText: str | None, newText: str | None)` — `currentText None` = file absent (create), `newText None` = delete the file.
3. Errors `TicketAlreadyExists` (409, `ticket_already_exists`) and `UnknownTicket` (404, `ticket_not_found`).
4. Path safety: validate the id via `TICKET_ID_PATTERN` and resolve `<ticketsDir>/<id>.md` under `project.rootPath`, raising `PathTraversal` on escape (`from factory_console.file_adapter.path_safety import PathTraversal`; mirror the `_safe_resolve` helper in `ticket_md.py`).
5. `render_create(project, draft) -> list[PlannedChange]`: read current entries via `load_manifest` (T12); if `draft.id` already present raise `TicketAlreadyExists`; append a new camelCase entry (id/title/status='todo'/track/milestone/dependsOn/provides scalar/files) and re-serialize tickets.json (2-space indent, trailing newline); render the `.md` (YAML front-matter from `draft.frontMatter` when non-empty + `bodyMarkdown`); insert a `- [ ] **<id>** — <title>` line under the matching `## <milestone>` section (reuse roadmap_parse T38 conventions; tolerant if no matching section — skip the roadmap change).
6. `render_edit(project, ticket_id, edit) -> list[PlannedChange]`: load the current entry (raise `UnknownTicket` if absent); MERGE edit fields onto the existing raw entry dict so unknown fields survive; re-serialize manifest; re-render `.md`; update the roadmap line text in place.
7. `render_delete(project, ticket_id) -> list[PlannedChange]`: remove the manifest entry (`UnknownTicket` if absent), mark the `.md` PlannedChange `newText=None`, remove the roadmap line.
8. Only ever emit the three known relPaths — never a run-state path. Import by full path; do NOT edit `file_adapter/__init__.py`.

## Critical files

- `server/factory_console/file_adapter/write_render.py` (new)
- `tests/unit/test_write_render.py` (new)

## Interface & data

`render_{create,edit,delete}(project, ...) -> list[PlannedChange]`. By reference: the tolerant `tickets.json` manifest via `load_manifest` (T12, camelCase entries; unknown fields preserved); the `.md` front-matter/body split + `TICKET_ID_PATTERN` + `PathTraversal` (from `file_adapter.path_safety`, T13); `ROADMAP.md` `## milestone` + checklist conventions from `roadmap_parse` (T38); consumes `TicketDraft`/`TicketEdit` (T55). No DB. NFR: forward-compat (preserve unknown manifest fields), path-safety containment, MUST NOT emit any run-state path. Errors: `TicketAlreadyExists` (409), `UnknownTicket` (404), `PathTraversal` (400).

## Verification

`pytest tests/unit/test_write_render.py` against tmp_path fixtures: create yields three PlannedChanges with a new manifest entry + `.md` + roadmap line and raises `TicketAlreadyExists` on a dup id; edit merges onto an existing entry and PRESERVES an unknown `estimate` field (assert it survives in the newText JSON); delete removes the entry, sets `.md` newText=None, drops the roadmap line, raises `UnknownTicket` for a missing id; an id escaping the root raises `PathTraversal`. Assert NO file is written.
