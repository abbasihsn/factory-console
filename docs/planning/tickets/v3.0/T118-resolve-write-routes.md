# [T118] Per-request resolution for the ticket write routes

milestone: v3.0 · track: backend · depends_on: T117, T65, T80, T92, T96 · provides: create/edit/delete ticket writes targeting the SELECTED project, with the selection checked after the write token and never failing open on an unresolvable selection.

## Context

The three write handlers in `tickets_write.py` each read `app.state.project_root` today. Converting
them is the last piece of the resolution change, and it is its own PR because it is the only one
where a resolution mistake writes to disk.

**Two orderings matter and are decided here.** First, the write token is checked BEFORE the
selection: an unauthenticated caller must learn only "not authorized", never which project is
selected or whether its path exists — the 401 stays as opaque as T64 made it. Second, MONOTONICITY
binds the resolution as hard as it binds the run-state gate: a selection that cannot be resolved (no
selection, unregistered id, vanished or unreadable path, unreadable registry) REFUSES the write with
the named 409. **It may never fall back to the pinned root** — "I could not establish which project
this is" must never be answered more permissively than "I know exactly which project this is", and
silently writing a ticket into the wrong repo is the worst available failure in this milestone.

The run-state write gates themselves (`ensure_mutable` / `ensure_deletable`, `MUTABLE_STATES` /
`DELETABLE_STATES`, the 409 wordings) are untouched — they already operate on whatever `Project` they
are handed, and per-project resolution is exactly what hands them a different one.

## Staged approach

1. EDIT `server/factory_console/api/v1/tickets_write.py` — all three handlers (`create_ticket`,
   `edit_ticket`, `delete_ticket`) take `root: Path = Depends(get_current_project_root)` in place of
   the `app.state.project_root` read; wrap `load_project` and the write-service call in
   `anyio.to_thread.run_sync(partial(...))` (their first conversion, and the writer does real disk
   I/O so it is squarely in scope). The router-level
   `dependencies=[Depends(require_write_token), Depends(reject_unknown_query_params)]` is untouched —
   FastAPI resolves router dependencies before the handler's own, which is what puts the 401 ahead of
   the 409 without any explicit sequencing.
2. Extend the module docstring with the two orderings above and with the MONOTONICITY argument for
   refusing rather than falling back.
3. EDIT `tests/integration/test_api_tickets_write.py` — for each of the three routes: no selection →
   `409 no_project_selected`; selected path deleted → `409 selected_project_unavailable`; **NO header
   and no selection → 401** (proving the token check comes first and leaks nothing); and **a write
   against a second selected project lands in THAT project's tree and leaves the first untouched** —
   the fail-open regression test.
4. Confirm `tests/integration/test_api_write_tickets.py` and `test_real_writer_roundtrip.py` still
   pass unmodified.

## Critical files

- `server/factory_console/api/v1/tickets_write.py` (modify)
- `tests/integration/test_api_tickets_write.py` (modify)

## Interface & data

Request/response shapes unchanged — `POST /api/v1/tickets`, `PATCH /api/v1/tickets/{id}`,
`DELETE /api/v1/tickets/{id}` keep their T65 bodies, status codes and the `X-Factory-Write-Token`
requirement.

New error responses (after the 401): `409 no_project_selected`, `409 selected_project_unavailable`.
The existing `409 ticket_not_mutable` wordings are unchanged and unaffected.

Contracts by reference: `require_write_token` (T64); the `FileWriter` port and the write gate
`ensure_mutable` / `ensure_deletable` with the `MUTABLE_STATES` / `DELETABLE_STATES` allowlists
(ARCHITECTURE.md → "The two write predicates"); `TicketIdPath` (`api/deps.py`); T111's
`SelectionFailure` union.

DB ops: a registry `SELECT` per request via the resolution seam; the console db is never written by
these routes. NFR flags: auth = write token, checked first; anyio offload added at all three handler
boundaries; no idempotency change; single-worker serialization of the write path unchanged.

## Verification

`python -m pytest tests/integration/test_api_tickets_write.py
tests/integration/test_api_write_tickets.py tests/integration/test_real_writer_roundtrip.py -q`,
then `python -m pytest -q`. `make lint`. Manual: boot pinned on a scratch copy of a fixture and
confirm an edit still round-trips.
