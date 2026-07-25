# [T55] Write-path domain models (TicketDraft, TicketEdit, DiffPreview, WriteResult)

milestone: v2 · track: file-adapter · depends_on: T07 · provides: domain/write.py — the canonical Pydantic write DTOs consumed by the FileWriter port, diff engine, backend endpoints, and (via OpenAPI) the SPA.

## Context

Every v2 write flows through a small set of request/response types. Define them once in the domain layer so the FileWriter port, the diff engine, and the API reference the SAME shapes (read-side pattern: domain models double as the shared-types contract). Pure models, no I/O — the foundation the rest of the track builds on. This is the single source of the write DTOs; the API endpoints consume these directly rather than defining a parallel `api/v1/write_models.py`.

## Staged approach

1. CREATE `server/factory_console/domain/write.py`.
2. `TicketDraft` (create): `id: TicketId`, `title: str`, `track: str | None = None`, `milestone: str | None = None`, `dependsOn: list[str] = []`, `provides: str = ''`, `files: list[str] = []`, `bodyMarkdown: str`, `frontMatter: dict[str, Any] = {}`; `model_config` frozen, `extra='forbid'`.
3. `TicketEdit` (edit): same fields as `TicketDraft` minus `id` (the id is the path param).
4. `FileDiff`: `path: str` (project-relative POSIX), `changeKind: Literal['create','modify','delete']`, `diff: str` (unified-diff text).
5. `DiffPreview`: `ticketId: str`, `files: list[FileDiff]`.
6. `WriteResult` (the uniform envelope): `applied: bool`, `ticketId: str`, `changedFiles: list[str]`, `diff: DiffPreview`, `ticket: Ticket | None`. Apply → `applied=true, ticket=<re-read Ticket>, changedFiles=<written paths>`. Dry-run → `applied=false, ticket=null, changedFiles=<planned paths>, diff=<preview>`.
7. Reuse `TicketId`/`TICKET_ID_PATTERN` from `domain.ticket` verbatim — do NOT restate the regex.
8. Do NOT re-export in `domain/__init__.py`; consumers import `from factory_console.domain.write import ...` by full path.

## Critical files

- `server/factory_console/domain/write.py` (new)
- `tests/unit/test_domain_write.py` (new)

## Interface & data

Pydantic models only — `TicketDraft`/`TicketEdit` (inbound), `DiffPreview`/`FileDiff`/`WriteResult` (outbound). Contracts by reference: reuses `TicketId`/`TICKET_ID_PATTERN` (domain.ticket, single source of truth); `domain.Ticket` as the `WriteResult.ticket` resource; camelCase field naming per REST v1 (ARCHITECTURE "Contracts → REST v1"). No DB (files are the source of truth). NFR: input validation at the Pydantic boundary (id regex, required fields); `frozen`/`extra='forbid'` consistent with existing domain models.

## Verification

`pytest tests/unit/test_domain_write.py`: `TicketDraft` rejects an id violating `TICKET_ID_PATTERN` (ValidationError) and accepts a valid id; missing required fields (title/bodyMarkdown) raise; `DiffPreview`/`WriteResult` serialize to camelCase JSON. No server run needed.
