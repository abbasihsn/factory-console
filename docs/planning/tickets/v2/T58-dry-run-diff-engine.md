# [T58] Dry-run diff engine — planned change-set to unified DiffPreview (no writes)

milestone: v2 · track: file-adapter · depends_on: T55, T57 · provides: file_adapter/write_diff.py — preview(ticket_id, planned_changes) -> DiffPreview, per-file unified diff of exactly what would change, computed without touching disk.

## Context

Powers the UI diff-preview modal and the API dry-run: show the user exactly what a create/edit/delete would change, per file, before they confirm. Pure and shared by both the fake and real writers, so the preview a user sees is computed by the same code the writer plans from — no drift between "what preview shows" and "what apply does".

## Staged approach

1. CREATE `server/factory_console/file_adapter/write_diff.py`.
2. Define `preview(ticket_id: str, planned: list[PlannedChange]) -> DiffPreview`: for each `PlannedChange`, compute `changeKind` — `currentText None` → 'create', `newText None` → 'delete', else 'modify'; build a unified diff via `difflib.unified_diff` over `(currentText or '').splitlines()` vs `(newText or '').splitlines()`, with `fromfile`/`tofile` set to `a/<relPath>` `b/<relPath>`, keepends normalized; wrap as `FileDiff(path=relPath, changeKind, diff='\n'.join(...))`.
3. Return `DiffPreview(ticketId=ticket_id, files=[...])` preserving planned order (manifest, md, roadmap).
4. Skip a `PlannedChange` where `currentText == newText` (no-op) so the preview only lists genuine changes.
5. Import by full path; do NOT edit `file_adapter/__init__.py`.

## Critical files

- `server/factory_console/file_adapter/write_diff.py` (new)
- `tests/unit/test_write_diff.py` (new)

## Interface & data

`preview(ticket_id: str, planned: list[PlannedChange]) -> DiffPreview`. Outputs `DiffPreview{ticketId, files: FileDiff[]}` (T55 models). By reference: `DiffPreview`/`FileDiff` (domain.write, T55); `PlannedChange` (write_render, T57); stdlib `difflib.unified_diff`. No DB. NFR: strictly read-only (computes text diffs; no filesystem writes) — this is the dry-run guarantee.

## Verification

`pytest tests/unit/test_write_diff.py`: a create PlannedChange (currentText=None) yields `changeKind='create'` with all-added hunk lines; a modify yields a diff with +/- lines; a delete (newText=None) yields `changeKind='delete'`; an unchanged pair is omitted; `DiffPreview.ticketId` + file order preserved. No I/O.
