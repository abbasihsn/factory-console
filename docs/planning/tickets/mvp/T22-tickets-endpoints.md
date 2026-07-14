# [T22] Tickets list + detail endpoints + TicketService

milestone: MVP · track: backend · depends_on: T20, T21 · provides: `GET /api/v1/tickets` (with status/track/milestone/q filters) + `GET /api/v1/tickets/{id}`

## Context

Two workhorse endpoints the SPA list + detail pages depend on. List returns filtered/searched summaries; detail returns full `Ticket` with rendered markdown + resolved run-state joined in. Logic lives in `services/ticket_service.py` so handlers stay tiny. Introduces the pattern service+handler that T23-T24 mirror.

## Staged approach

1. `server/factory_console/services/__init__.py`.
2. `services/ticket_service.py`: `class TicketService(adapter: FileAdapter)`.
   - `list_tickets(project, *, status: str|None, track: str|None, milestone: str|None, q: str|None) -> list[TicketSummary]` — calls `adapter.list_tickets(project)`; filters (equality on status/track/milestone; `q` substring case-insensitive over id+title); for each result sets `runState` via `adapter.read_run_state`.
   - `get_ticket(project, ticket_id) -> Ticket` — calls `adapter.get_ticket`; if `None` raise `TicketNotFound(ticket_id)` (defined here, inherits `FactoryConsoleError`, status 404 `code=ticket_not_found`); else set `runState` + return.
3. `api/v1/tickets.py` with `GET /tickets` returning `{ items: list[TicketSummary], total: int }` accepting `Query` params; `GET /tickets/{ticket_id}` with `ticket_id: str = Path(..., pattern=TICKET_ID_PATTERN)` imported from `domain.ticket` (NOT redefined).
4. Include tickets router.
5. `tests/unit/test_ticket_service.py` against `FakeFileAdapter`: filter combos; `q` case-insensitive; `get_ticket None -> TicketNotFound`; `run_state` joined on both paths.
6. `tests/integration/test_api_tickets.py` against `FakeFileAdapter` + `RealFileAdapter` over fixtures: happy paths; unknown id -> 404 envelope; invalid id (traversal chars) -> 400 `code=invalid_ticket_id` envelope (via T20's special-case); filter query returns expected subset.

## Critical files

- `server/factory_console/services/__init__.py`
- `server/factory_console/services/ticket_service.py`
- `server/factory_console/api/v1/tickets.py`
- `server/factory_console/app.py`
- `tests/unit/test_ticket_service.py`
- `tests/integration/test_api_tickets.py`

## Interface & data

Implements REST v1 `/api/v1/tickets` + `/api/v1/tickets/{id}`. `Path` param uses `TICKET_ID_PATTERN` from `domain/ticket.py` (single source of truth). `FileAdapter` methods used: `list_tickets, get_ticket, read_run_state`. Errors: `TicketNotFound` (T22-owned) -> 404; invalid id -> 400 `code=invalid_ticket_id`; `MalformedManifest` -> 500.

## Verification

pytest new test files green; manual against `with_run_state` fixture: `curl '?status=todo'` returns only todo tickets with `runState` populated; unknown id -> envelope 404; traversal id -> 400; ruff clean.
