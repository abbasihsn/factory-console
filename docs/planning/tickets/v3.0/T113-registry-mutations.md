# [T113] Registry mutations: POST /projects, DELETE /projects/{id}, PUT /projects/current

milestone: v3.0 · track: backend · depends_on: T112, T64, T65 · provides: add, remove and select registered projects over HTTP — all three gated by the v2 `X-Factory-Write-Token`.

## Context

**All three registry mutations require `X-Factory-Write-Token`, and a missing or wrong header is the
same opaque `401 write_token_invalid` the SPA's existing `WriteTokenPrompt` already handles.**

The argument, on the record. The token exists as defence-in-depth BEHIND the loopback boundary, to
stop another local process or a drive-by browser request from making the console act. The usual
objection — "registry writes mutate the console's own db, not the project's files" — is exactly
backwards on capability. `POST /api/v1/projects {path}` makes the console open an arbitrary absolute
path on this machine and then serve that project's contents to anyone who can reach the port; it is
an arbitrary-path READ primitive, strictly larger than editing one ticket in an already-chosen
project. And it is reachable by CSRF: the console runs no CORS policy and no CSRF token, so any page
in the user's browser could POST to `127.0.0.1` and, unguarded, would silently register `$HOME` and
make it readable over HTTP.

`PUT /projects/current` is a weaker case on its own — it only selects among already-registered paths
— but it is gated too, deliberately and for one stated reason: it changes what EVERY read endpoint
returns for every client, so an unguarded switch is a way to make an operator read the wrong
project's board while believing it is theirs. The cost is one header the SPA already sends on every
ticket write, and T121–T124 route the 401 through the existing prompt so the user experience is the
one they already know. **This is the decision; it is not revisited per-endpoint elsewhere.**

`DELETE` destroys durable console state, so it is gated for the ordinary reason.

The reads (`GET /projects`, `GET /projects/current`) stay ungated: they disclose nothing the loopback
boundary does not already permit, and they are how an operator diagnoses a bad selection.

## Staged approach

1. EDIT `server/factory_console/api/v1/projects.py` (same module as the reads — one endpoint family,
   one file):
   - Request models: `AddProjectRequest { path: str, name: str | None = None }`,
     `SelectProjectRequest { projectId: str }`, both `extra="forbid"`.
   - `POST /projects`, status 201, `dependencies=[Depends(require_write_token)]` +
     `openapi_extra={"security": [{WRITE_TOKEN_SCHEME_NAME: []}]}` — the same pair
     `tickets_write.py` uses, because `require_write_token` is a plain dependency FastAPI cannot
     infer a scheme from. In one offloaded hop: reject a non-absolute path
     (`400 invalid_project_path`, from `store/paths.py`); reject a path that is not an App Factory
     project by calling `adapter.load_project(path)` and mapping `ProjectNotFound` /
     `MalformedManifest` through the registered error handlers; reject a duplicate resolved path
     (`409 duplicate_project_path`); else `registry.add_project(...)` with `name` defaulting to the
     directory name. Returns `RegisteredProjectOut`.
   - `DELETE /projects/{id}` → 204, same gate. The reserved `session` id →
     `409 session_project_not_removable` (it was never registered). Unknown id →
     `404 project_not_registered`. If the removed project was selected, the schema's
     `ON DELETE SET NULL` clears the persisted selection; the handler additionally clears the
     process-local session selection via `selection.select(None)` — which fires the on-change hook,
     so T114's supervisor releases the watcher — and subsequent project-scoped reads answer
     `409 no_project_selected`, **never a silent fallback to another project**.
   - `PUT /projects/current` → `CurrentSelectionResponse`, same gate. Unknown id →
     `404 project_not_registered`; the reserved `session` id is allowed only when a pinned root
     exists. **A degraded `condition` is NOT a precondition for selecting**: selecting a project
     whose path has gone away must be possible so the operator can then delete it, and the resulting
     reads answer the named `selected_project_unavailable` 409 rather than the switch failing
     opaquely.
2. EDIT `tests/integration/test_api_projects.py` — per route: no header → 401; wrong header → 401;
   valid → effect. Plus: relative path, non-project path, duplicate, delete-the-selected clears the
   selection, select-unknown 404, select-a-missing-path succeeds and the follow-up `/project` gives
   `selected_project_unavailable`.
3. EDIT `tests/integration/test_api_write_token.py` — extend its coverage table with the three new
   gated operations so "which routes are gated" stays asserted in one place.

## Critical files

- `server/factory_console/api/v1/projects.py` (modify)
- `tests/integration/test_api_projects.py` (modify)
- `tests/integration/test_api_write_token.py` (modify)

## Interface & data

```
POST   /api/v1/projects          { path: string, name?: string|null } -> 201 RegisteredProjectOut
DELETE /api/v1/projects/{id}                                          -> 204 (no body)
PUT    /api/v1/projects/current  { projectId: string }                -> 200 CurrentSelectionResponse
```

All three require header `X-Factory-Write-Token` (`config.WRITE_TOKEN_HEADER`); missing/empty/wrong →
`401 { error: { code: "write_token_invalid", message } }`, identical to the ticket-write routes.

Error codes (all in the standard envelope): `invalid_project_path` 400 · `project_not_found` 400
(path is not an App Factory project) · `malformed_manifest` (existing code) ·
`duplicate_project_path` 409 · `project_not_registered` 404 · `session_project_not_removable` 409 ·
`write_token_invalid` 401.

Contracts by reference: `require_write_token` + `WRITE_TOKEN_SCHEME_NAME` (`api/write_token.py`,
T64); `FileAdapter.load_project` for validating a candidate path; `ProjectRegistry.add_project` /
`remove_project` / `set_selected_project` (T106).

DB ops: `INSERT` and `DELETE` on `projects`; `UPDATE console_state.selected_project_id` via the port.
NFR flags: auth = write token on all three mutations, none on the reads; every `sqlite3` and
path-probe call offloaded with `anyio.to_thread.run_sync(partial(...))`; **NOT idempotent by design**
— a duplicate POST is a named 409 rather than a silent no-op, so the SPA can say so; no rate limit
(single-user loopback, consistent with the ticket-write routes).

## Verification

`python -m pytest tests/integration/test_api_projects.py
tests/integration/test_api_write_token.py -q`, then `python -m pytest -q`. `make lint`.
Manual: boot, copy the token off stderr, POST a fixture path — confirm 401 without the header and 201
with it, and that `GET /api/v1/projects` then lists the new row.
