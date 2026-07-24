# [T41] GET /api/v1/search endpoint + SearchService (cross-ticket full-text search)

milestone: v1 · track: backend · depends_on: T36, T22, T17, T10 · provides: GET /api/v1/search?q= returning full-text SearchHit results wrapped in a SearchResponse envelope

## Context

v1 adds a global search box; the frontend needs a real cross-ticket full-text endpoint that searches ticket BODIES, not just id+title. This is distinct from T22's list `?q=` filter (a case-insensitive substring over id+title only) — this endpoint delegates to the file-adapter's search capability (T36) which reads the bodies. Delivers the backend half of the v1 search epic; the frontend consumes `SearchHit` via regenerated OpenAPI types.

## Staged approach

1. Create `server/factory_console/services/search_service.py`: a `SearchService(adapter)` constructed per request exposing `search(project, query, *, limit) -> list[SearchHit]`, normalizing the query (strip; blank/whitespace-only → return `[]`) and delegating the actual matching to the adapter's `search_tickets`; no filesystem access, mirroring `TicketService`/`DepsService`.
2. Create `server/factory_console/api/v1/search.py`: a tags-only `APIRouter` (mirror `api/v1/tickets.py`) defining a backend-owned `SearchResponse` envelope `BaseModel` (frozen, `extra='forbid'`) `{ items: list[SearchHit], total: int }` (`SearchHit` imported from `domain`, NOT redefined) and an `async def search(request, q, limit, adapter=Depends(get_file_adapter)) -> SearchResponse` handler that loads the project from `app.state.project_root` and delegates to `SearchService`.
3. Append `router.include_router(search_router)` + its import to `api/v1/__init__.py` (the shared router registry — declared in critical_files so the sprint serializes this against T42/T45 that also edit it).

## Critical files

- `server/factory_console/services/search_service.py` (new)
- `server/factory_console/api/v1/search.py` (new)
- `server/factory_console/api/v1/__init__.py` (register the sub-router)

## Interface & data

- Request: `GET /api/v1/search?q=<str, required>&limit=<int, default 50, ge=1 le=200>`. Response 200: `SearchResponse { items: SearchHit[], total }` (camelCase JSON).
- `SearchHit` is the T36 file-adapter model (referenced by name, not redefined): `{ ticket: TicketSummary, score, matchedFields[] }`.
- Consumes: `FileAdapter.search_tickets` (T36), `Project`, `TicketSummary`. No DB. NFR: no cache (re-read per request), no auth (127.0.0.1), server-side query, blank `q` → empty result (no 422). Explicitly NOT a duplicate of T22's id+title substring `?q=` filter.

## Verification

`pytest` integration test with `httpx.AsyncClient` over `create_app(FakeFileAdapter seeded with searchable ticket bodies)`: assert `GET /api/v1/search?q=<term>` returns 200 with matching items + correct total; blank/whitespace `q` returns `{items:[], total:0}`; out-of-range `limit` returns the `validation_error` envelope; a term matching a body but not id/title still hits (proving body coverage vs T22). Unit-test `SearchService` against `FakeFileAdapter` for query normalization + limit passthrough. Confirm the `SearchHit` + `SearchResponse` schemas appear in `/api/v1/openapi.json`.
