# [T73] Integration tests for write endpoints: create/edit/delete, token auth, non-todo rejection, dry-run, error envelope

milestone: v2 · track: testing · depends_on: T35, T63, T64, T65 · provides: httpx/TestClient coverage of create/edit/delete happy paths, token auth (401), non-todo rejection, dry-run diff, uniform error envelope.

## Context

The write endpoints are the console's only mutating surface and must be locked down at the API boundary. This ticket extends the integration suite to prove the endpoints behave exactly per the REST v1 contract: authorized todo-ticket writes succeed and persist, unauthorized calls are rejected before any write, non-todo tickets are refused, and dry-run previews a diff while writing nothing — all failures returning the single shared error envelope.

## Staged approach

1. Create `tests/integration/test_api_write_tickets.py` following `test_api_tickets.py`'s `_fake_app()` / `_real_app()` + TestClient structure.
2. Build the app with a `FakeFileWriter`/`FakeFileAdapter` seeded with a todo ticket (writable) and a non-todo ticket (e.g. `RunState.ready`) and a valid write token configured.
3. Auth: POST/PUT/DELETE with no token → 401, and with a wrong token → 401 (`write_token_invalid`, per T64), asserting `error.code`/status and that the adapter recorded zero writes.
4. Happy paths: create a new todo ticket (201), edit a todo ticket (200), delete a todo ticket (200); assert the state reflects the change and the response envelope shape.
5. Non-todo rejection: PUT/DELETE a ready ticket → `ticket_not_mutable` 409; assert no write occurred.
6. Dry-run: POST/PUT with `?dryRun=true` returns a diff payload and status 200 while the adapter records no mutation.
7. OpenAPI: assert the three new paths + request/response schemas are published (mirrors `test_api_tickets.py`'s openapi test). Add one realism happy-path via `RealFileAdapter`/`RealFileWriter` over a `tmp_path` copy of `tests/fixtures/projects/with_run_state` so a real file is written and re-read.

## Critical files

- `tests/integration/test_api_write_tickets.py` (new)

## Interface & data

Endpoints under test (by reference): `POST /api/v1/tickets`, `PUT /api/v1/tickets/{id}`, `DELETE /api/v1/tickets/{id}` (camelCase JSON, ISO-8601). Auth: the per-session loopback write-token header `X-Factory-Write-Token` (T64). Error contract: the single `{error:{code,message,details?}}` envelope. Entities: `Ticket`, `RunState`, `TICKET_ID_PATTERN`. No DB — filesystem writes via the co-writer, asserted through fake call records + a tmp_path re-read. NFR: auth scope (write token), idempotency/dry-run, run-state authorization.

## Verification

`pytest -q tests/integration/test_api_write_tickets.py`; part of `pytest -q --cov=factory_console` (>=85% gate). Fake-adapter cases need no filesystem; the realism case writes only under `tmp_path`.
