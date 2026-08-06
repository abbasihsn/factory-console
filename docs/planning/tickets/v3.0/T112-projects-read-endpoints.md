# [T112] GET /api/v1/projects + GET /api/v1/projects/current

milestone: v3.0 · track: backend · depends_on: T111, T110, T102 · provides: the two read endpoints the SPA's project dropdown is built from — the registered projects with their per-row `condition`, and the current selection — plus the OpenAPI schemas the frontend generates its types from.

## Context

The SPA needs the registry over HTTP, with published shapes to generate types from. This ticket
answers the READ half; the mutations follow in T113 so the write-token argument gets its own
reviewable diff.

Each row carries a **`condition`** (T103's exhaustive union, established from disk by T109 and
joined by T110) — not a boolean. A hosted console shows run-state and artefacts only for projects
whose working copies are on this machine, and "the path is not here" must not be conflated with "I
could not look at the path" (MONOTONICITY). The field is named `condition` everywhere: in the domain
type, on the wire, and in the SPA's label map. There is no second name for it.

`GET /projects/current` returns `selected: null` with a NAMED `reason` from T111's shared
`SelectionFailure` union rather than a 404 — no selection is a normal state a fresh console starts
in, and the SPA renders it as a prompt, not an error.

A `factory-console PATH` boot appears in the list as the reserved `session` row with
`registered: false`, so the dropdown is populated from the very first boot and the SPA can offer
"Add this project" as an explicit act.

## Staged approach

1. CREATE `server/factory_console/api/v1/projects.py`:
   - `router = APIRouter(tags=["projects"])`, matching the sibling modules (the package `__init__`
     owns the `/api/v1` prefix).
   - `ProjectIdPath = Annotated[str, PathParam(pattern=REGISTERED_PROJECT_ID_PATTERN)]`, mirroring
     how `TicketIdPath` constrains an id at the FastAPI boundary. The id's FORM is T103's; this only
     bounds what may reach the registry from HTTP.
   - Response models, all `ConfigDict(frozen=True, extra="forbid")` like the existing wire models:
     `RegisteredProjectOut { id, name, path, addedAt, registered, selected, condition }`;
     `ProjectListResponse { items, total }`;
     `CurrentSelectionResponse { selected: RegisteredProjectOut | None, reason: SelectionFailure | None }`.
   - `RegisteredProjectOut.from_entry(...)` is the ONE constructor — the disclosure boundary for a
     registry row, exactly as `ProjectedArtifactRead.from_artifact` is for an artefact. It names its
     fields explicitly and does NOT `model_dump()` the store entity, so a column the store track adds
     later cannot reach the wire by accident.
2. `async def list_projects(...)`: reads the registry through `Depends(get_project_registry)` and the
   selection through `Depends(get_selection_state)`; **one** `anyio.to_thread.run_sync` hop performs
   the `list_projects()` query AND `resolve_entries(...)` together (one offload, not N). Prepends the
   `session` row when a pinned root exists. A `None` registry yields just the session row — pinned
   mode is a valid configuration, not an error.
3. `async def get_current(...)`: resolves the selected row the same way and returns the envelope,
   with exactly one of `selected` / `reason` set (the one-of discipline `ArtifactRead` uses).
4. EDIT `server/factory_console/api/v1/__init__.py` — one import + one `include_router` line.
   **AGGREGATION FILE**, declared here and in T139.
5. CREATE `tests/integration/test_api_projects.py` — empty registry; pinned-only app (session row
   present, `registered: false`); several rows with one path deleted and one unreadable, asserting
   the distinct `condition` values; `/projects/current` with and without a selection; an
   `extra="forbid"` round-trip.
6. Confirm `tests/integration/test_disclosure_policy.py` passes UNTOUCHED — the generic sweep should
   accept these models with no allowlist entry, because nothing here is free-form. Only enrol the new
   models if the sweep needs it.

## Critical files

- `server/factory_console/api/v1/projects.py` (create)
- `server/factory_console/api/v1/__init__.py` (modify — aggregation file)
- `tests/integration/test_api_projects.py` (create)

## Interface & data

`GET /api/v1/projects` → `ProjectListResponse`:

```
{ items: [ { id: string, name: string, path: string,
             addedAt: string|null,            // ISO-8601; null for the session row
             registered: boolean,             // false only for id === "session"
             selected: boolean,
             condition: "ok"|"path_missing"|"not_a_project"|"unreadable"|"no_factory_dir" } ],
  total: number }
```

`GET /api/v1/projects/current` → `CurrentSelectionResponse`:

```
{ selected: RegisteredProjectOut | null,
  reason: "no_selection"|"selected_project_not_registered"
        | "selected_project_missing"|"selected_project_unreadable" | null }
```

Exactly one of `selected` / `reason` is set.

Contracts/entities by reference: ARCHITECTURE.md "Data-model additions (v3)" `RegisteredProject`
(T103's; narrowed here, not redefined); the `{items,total}` envelope matching `/tickets`, `/search`,
`/runs`; T111's `SelectionFailure` union (reused verbatim, not re-spelled); the disclosure rule — the
registry row is console-owned rather than factory-written, but the narrowing constructor keeps the
same shape so a later store column cannot leak.

DB ops: read-only — `SELECT` over `projects` through the port, plus one `stat` per row via the
condition probe. No migration. NFR flags: offloaded (`sqlite3` + N stats in a single `to_thread`
hop); **NO write token** — reads are gated only by the loopback boundary, like every other read; no
cache (the SPA calls this on switch, and `condition` must be current). Absolute host paths are on the
wire; that is the existing precedent set by `/health`'s `projectRoot` under the same trust boundary.

## Verification

`python -m pytest tests/integration/test_api_projects.py
tests/integration/test_disclosure_policy.py -q`; then `python -m pytest -q`. `make lint`.
Manual: boot on a fixture, `curl -s localhost:PORT/api/v1/projects` shows the `session` row, and
`curl -s localhost:PORT/api/v1/openapi.json` contains `ProjectListResponse` +
`CurrentSelectionResponse` so the SPA's `openapi-typescript` codegen picks them up.
