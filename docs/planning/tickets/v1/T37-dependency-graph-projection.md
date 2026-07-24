# [T37] Dependency graph (DAG) projection behind the FileAdapter port

milestone: v1 · track: file-adapter · depends_on: T07, T10, T17, T23 · provides: FileAdapter.get_graph → TicketGraph (run-state-colored nodes + dependsOn edges) + GraphNode/GraphEdge/TicketGraph models, reusing the shared TicketProjection

## Context

v1 adds a rendered dependency-graph route `/graph` (Cytoscape.js DAG colored by run-state). The graph is a whole-project projection — every ticket a node, every resolved `dependsOn` an edge — a new shape distinct from the per-ticket `DepNeighborhood` the MVP deps view serves. It MUST reuse the shared `TicketProjection` (introduced with T17/T23) so node run-state and edges can never drift from the list/deps views; it must NOT re-implement the reverse-index. Delivers the nodes+edges payload `GET /api/v1/graph` (T42) returns; the frontend maps it to Cytoscape element format (not this ticket's concern).

## Staged approach

1. Add `server/factory_console/domain/graph.py`: frozen Pydantic `GraphNode { id: TicketId, title, status, track, milestone, runState: RunState }`, `GraphEdge { source: str, target: str }`, `TicketGraph { nodes: list[GraphNode], edges: list[GraphEdge] }`. Import by full path; do NOT add to `domain/__init__.py`.
2. Add a small read-only accessor to `file_adapter/projection.py`: `all_tickets(self) -> list[Ticket]` returning `list(self._tickets)` — only this ticket edits `projection.py` in v1, so no collision.
3. Add `server/factory_console/file_adapter/graph.py`: pure `build_graph(projection: TicketProjection) -> TicketGraph`. Nodes from `projection.summaries()` (run-state already resolved by the shared projection); edges from each `projection.all_tickets()` ticket's `dependsOn`, de-duplicated (`dict.fromkeys`), keeping ONLY edges whose target resolves to a known node and skipping self-loops (source==target) so the result is a clean DAG Cytoscape can render — mirroring how `neighborhood()` separates `unresolvedDeps`.
4. Add `get_graph` to the `FileAdapter` Protocol in `protocol.py` (shared with T36 — listed so the overlap filter serializes them).
5. `real.py`: `get_graph` builds the shared per-request `TicketProjection` via the existing `_project_manifest(project)` and returns `build_graph(projection)`.
6. `fake.py`: `get_graph` returns `build_graph(self._projection)`.

## Critical files

- `server/factory_console/domain/graph.py` (new — GraphNode/GraphEdge/TicketGraph)
- `server/factory_console/file_adapter/graph.py` (new — pure build_graph)
- `server/factory_console/file_adapter/projection.py` (add all_tickets accessor)
- `server/factory_console/file_adapter/protocol.py` (add get_graph to the Protocol)
- `server/factory_console/file_adapter/real.py` (implement)
- `server/factory_console/file_adapter/fake.py` (implement)

## Interface & data

- `FileAdapter.get_graph(project: Project) -> TicketGraph`; pure `build_graph(projection: TicketProjection) -> TicketGraph`.
- Touched BY REFERENCE (do not redefine): the `FileAdapter` Protocol (adds one method), the shared `TicketProjection` (`summaries()` + new `all_tickets()`) owned via T17/T23 — reused, not duplicated — and `RunState`/`TicketSummary` from `domain`.
- New models `GraphNode`/`GraphEdge`/`TicketGraph` (frozen, `extra='forbid'`, camelCase). Node ~`{ id, title, status, track, milestone, runState }`, edge ~`{ source, target }` (source depends on target). Edge semantics: only resolved (both-endpoints-present) edges emitted; self-loops dropped; dangling `dependsOn` ids are intentionally NOT edges — consistent with `DepNeighborhood.unresolvedDeps`.
- DB ops: N/A. NFR: no cache / re-read per request; read-only.

## Verification

`pytest` unit tests for `build_graph` on constructed projections: node count == ticket count with `runState` carried through; edges only between known nodes; dangling dep → no edge; self-loop dropped; duplicate `dependsOn` collapses to one edge. Adapter tests: `FakeFileAdapter.get_graph` and `RealFileAdapter.get_graph` (against `tests/fixtures/projects/with_run_state`) return matching node/edge sets whose node `runState` equals `list_tickets` summaries (no drift). `isinstance` checks against `FileAdapter` still hold. `file_adapter/` coverage >90%.
