# [T13] Ticket .md + front-matter parser (ticket_md.py + PathTraversal + TicketFileMissing)

milestone: MVP · track: file-adapter · depends_on: T07, T08 · provides: `read_ticket_md(project, id)`; `enrich_ticket(project, stub)`; `PathTraversal` (FactoryConsoleError, status 400, code `invalid_ticket_id`); `TicketFileMissing` (status 404, code `ticket_file_missing`)

## Context

Ticket bodies live at `docs/planning/tickets/<id>.md`. Reads one `.md` by ticket id, separates optional YAML front-matter from body markdown. Enforces defense-in-depth: resolved absolute path MUST be under `project.rootPath`; anything else raises `PathTraversal` (even though T07's `TicketId` type validates at the boundary).

## Staged approach

1. `file_adapter/ticket_md.py`.
2. `class PathTraversal(FactoryConsoleError)` and `class TicketFileMissing(FactoryConsoleError)`.
3. `_safe_resolve(project, ticket_id) -> Path`: re-validate `ticket_id` against `TICKET_ID_PATTERN` (raise `PathTraversal`); `candidate = (project.ticketsDir / f'{ticket_id}.md').resolve(strict=False)`; verify `candidate.is_relative_to(project.rootPath.resolve())` (else raise `PathTraversal`).
4. `read_ticket_md(project, ticket_id) -> tuple[dict, str]`: `(front_matter_dict, body_markdown)`; YAML fences `---\n...\n---\n` via PyYAML `safe_load`; absent fm -> `({}, full_text)`; missing file -> `TicketFileMissing`. Add PyYAML to pyproject deps (T02 already lists it — confirm).
5. `enrich_ticket(project, stub) -> Ticket`: reads `.md`, sets `bodyMarkdown` + `filePath`, merges front-matter into `raw['frontMatter']` (manifest fields win). Returns new frozen `Ticket` via `model_copy`.
6. `tests/unit/test_ticket_md.py`: fm present + absent; malformed YAML -> fallback `({}, full_text)` without crashing; `'../etc/passwd'` -> `PathTraversal`; slash-id -> `PathTraversal`; missing file -> `TicketFileMissing`; symlink escaping root -> `PathTraversal`.

## Critical files

- `server/factory_console/file_adapter/ticket_md.py`
- `tests/unit/test_ticket_md.py`
- `pyproject.toml`

## Interface & data

Implements `ARCHITECTURE.md` `cross_cutting.input-validation` (ticket-id regex + path-traversal refusal). Uses `open('r')`. NFR: read-only, defense-in-depth path safety.

## Verification

`pytest tests/unit/test_ticket_md.py -q` green including traversal cases; ruff clean.
