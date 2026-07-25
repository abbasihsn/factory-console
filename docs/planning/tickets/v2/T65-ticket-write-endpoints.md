# [T65] POST/PUT/DELETE /api/v1/tickets write endpoints with ?dryRun diff-preview

milestone: v2 · track: backend · depends_on: T20, T22, T55, T62, T63, T64 · provides: POST /api/v1/tickets, PUT /api/v1/tickets/{id}, DELETE /api/v1/tickets/{id} with ?dryRun, token-gated, published in OpenAPI (consuming domain/write.py models directly).

## Context

The user-facing v2 slice: the three write verbs the SPA edit form calls. Each returns a uniform `WriteResult` carrying the unified diff (so the diff-preview modal and the post-save confirmation share one shape), honours `?dryRun=true` to preview without writing, is gated by the per-session write token, and enforces the todo-only editing rule through `WriteService`. Because these are ordinary FastAPI routes with typed models, they auto-publish into `/api/v1/openapi.json`, letting the frontend regenerate TS types with no extra backend work. Request/response bodies are the canonical `domain/write.py` models (no separate api-model layer).

## Staged approach

1. CREATE `api/v1/tickets_write.py` with `router = APIRouter(tags=['tickets'], dependencies=[Depends(require_write_token)])` so the token guard applies to every route here (and only here).
2. Handlers mirror the read-tickets structure — load `project = adapter.load_project(root)`, construct `WriteService(get_file_writer(...), adapter)`, delegate, return `WriteResult`:
   - `POST /tickets` (body `TicketDraft`, `dryRun: bool = Query(False)`) → 201 when applied / 200 on dry-run;
   - `PUT /tickets/{ticket_id}` (path pattern `TICKET_ID_PATTERN`, body `TicketEdit`, `dryRun`) → 200;
   - `DELETE /tickets/{ticket_id}` (`dryRun`) → 200 with `ticket=null`.
3. Do NO error handling in handlers — `TicketNotFound` / `TicketNotMutable` / `WriteConflict` / `WriteValidationError` / `WriteTokenInvalid` / `invalid_ticket_id` all flow to the existing handlers.
4. In `api/v1/__init__.py`, add `from .tickets_write import router as tickets_write_router` and one `router.include_router(tickets_write_router)` line (this is the shared aggregation file — edited by exactly this ticket).
5. Confirm the routes appear in the generated schema.

## Critical files

- `server/factory_console/api/v1/tickets_write.py` (new)
- `server/factory_console/api/v1/__init__.py`

## Interface & data

`POST /api/v1/tickets` (`TicketDraft` + `?dryRun`) → `WriteResult` (201 applied / 200 dry-run); `PUT /api/v1/tickets/{id}` (`TicketEdit` + `?dryRun`) → `WriteResult` 200; `DELETE /api/v1/tickets/{id}` (`?dryRun`) → `WriteResult` 200. All require header `X-Factory-Write-Token`. By reference: `WriteResult`/`TicketDraft`/`TicketEdit`/`DiffPreview` (T55), `WriteService` (T63), `get_file_writer` (T62), `require_write_token` (T64), `TICKET_ID_PATTERN`, REST v1 error envelope. No DB (files via writer tmp-write+rename). NFR: AUTH (router-level write-token dependency); run-state editing gate (409); read routes remain token-free; new routes auto-published in `/api/v1/openapi.json` for TS regeneration.

## Verification

`pytest tests/integration` with `httpx.AsyncClient` + `FakeFileWriter`/`FakeFileAdapter`: create/edit/delete on a todo fixture returns `WriteResult{applied:true}` with a non-empty diff and the change is observable via `GET /tickets/{id}`; `?dryRun=true` returns `applied:false` and leaves the fixture unchanged; edit/delete on a non-todo ticket returns `ticket_not_mutable` 409; create on an existing id returns `write_conflict` 409; missing/wrong `X-Factory-Write-Token` returns `write_token_invalid` 401; invalid id returns `invalid_ticket_id` 400. Assert the three routes appear in `GET /api/v1/openapi.json`. ruff + coverage gate (85%) hold.
