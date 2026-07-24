# [T42] GET /api/v1/graph endpoint + GraphService (dependency-DAG projection)

milestone: v1 · track: backend · depends_on: T37, T23, T17, T10 · provides: GET /api/v1/graph returning a DependencyGraph (nodes with run-state + dependsOn edges) for the frontend Cytoscape render

## Context

v1 adds the rendered dependency-graph route `/graph`; the Cytoscape.js render needs the whole DAG in one payload — every ticket a node carrying its run-state (for coloring), every `dependsOn` an edge. The existing endpoints can't supply this cheaply (list summaries omit edges; per-ticket `get_deps` would be N calls), so the file-adapter provides a single graph projection (T37) and this endpoint exposes it. Delivers the backend half of the v1 graph epic.

## Staged approach

1. Create `server/factory_console/services/graph_service.py`: a `GraphService(adapter)` constructed per request exposing `get_graph(project) -> TicketGraph`, delegating straight to the adapter's `get_graph`; a thin orchestrator with no I/O, mirroring `DepsService`.
2. Create `server/factory_console/api/v1/graph.py`: a tags-only `APIRouter` (mirror `api/v1/project.py`) with `async def get_graph(request, adapter=Depends(get_file_adapter)) -> TicketGraph` that loads the project from `app.state.project_root` and delegates to `GraphService`; returns the `TicketGraph` domain model directly (imported from `domain`, NOT redefined) so OpenAPI publishes nodes+edges.
3. Append `router.include_router(graph_router)` + its import to `api/v1/__init__.py` (shared router registry — declared in critical_files so it serializes against T41/T45).

## Critical files

- `server/factory_console/services/graph_service.py` (new)
- `server/factory_console/api/v1/graph.py` (new)
- `server/factory_console/api/v1/__init__.py` (register the sub-router)

## Interface & data

- Request: `GET /api/v1/graph` (no params). Response 200: `TicketGraph { nodes: GraphNode[], edges: GraphEdge[] }` (camelCase).
- `GraphNode`/`GraphEdge`/`TicketGraph` are the T37 file-adapter models (referenced, not redefined). Node ~`{ id, title, status, track, milestone, runState }`, edge ~`{ source, target }` (source depends on target). Per the T37 projection, edges to unresolved dep ids are excluded so no edge references a missing node — the backend returns the projection verbatim and does no re-shaping.
- Consumes: `FileAdapter.get_graph` (T37), `Project`. No DB. NFR: no cache (re-read per request), no auth (loopback).

## Verification

`pytest` integration test with `httpx.AsyncClient` over `create_app(FakeFileAdapter seeded with a small dependency web incl. a dangling dep)`: assert `GET /api/v1/graph` returns 200 with one node per manifest ticket, each carrying `runState`, and one edge per resolved `dependsOn`; assert no edge points at an unknown node id. Unit-test `GraphService` delegates to the adapter. Confirm `TicketGraph` appears in `/api/v1/openapi.json` for the frontend types.
