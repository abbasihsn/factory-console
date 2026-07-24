"""Pure whole-project dependency-graph builder over the shared projection.

``build_graph`` turns a
:class:`~factory_console.file_adapter.projection.TicketProjection` into the
:class:`~factory_console.domain.graph.TicketGraph` the ``/graph`` route renders,
REUSING the projection both adapters already share — so node run-state can never
drift from the list or deps views and the reverse index is never re-implemented.

Nodes come straight from :meth:`TicketProjection.summaries` (run-state already
resolved by the projection), in list order. Edge semantics, pinned here and
mirrored on :class:`~factory_console.domain.deps.DepNeighborhood`:

* RESOLVED-ONLY — an edge is emitted only when its ``target`` resolves to a known
  node; a dangling ``dependsOn`` id is intentionally NOT an edge (the same choice
  ``unresolvedDeps`` makes), so no edge can point at a non-node.
* SELF-LOOPS DROPPED — a ticket that lists itself yields no ``source == target``
  edge (a ticket is never its own dependency edge).
* DUPLICATES COLLAPSED — a ticket's repeated ``dependsOn`` id collapses to ONE
  edge (order-preserving), the same ``dict.fromkeys`` de-dupe
  :meth:`TicketProjection.neighborhood` uses; the de-dupe is per ticket
  ``(source, target)`` pair.
"""

from __future__ import annotations

from factory_console.domain.graph import GraphEdge, GraphNode, TicketGraph
from factory_console.file_adapter.projection import TicketProjection


def build_graph(projection: TicketProjection) -> TicketGraph:
    """Project ``projection`` to the whole-project run-state-coloured dependency DAG.

    Nodes are the projection's summaries (run-state already resolved), in list
    order; edges are each ticket's ``dependsOn`` relations, de-duplicated with
    ``dict.fromkeys`` and kept ONLY when the target resolves to a known node and
    is not the ticket itself — see the module docstring for the resolved-only,
    self-loop-free, duplicates-collapsed edge semantics.
    """
    nodes = [
        GraphNode(
            id=summary.id,
            title=summary.title,
            status=summary.status,
            track=summary.track,
            milestone=summary.milestone,
            runState=summary.runState,
        )
        for summary in projection.summaries()
    ]
    known_ids = {node.id for node in nodes}
    edges = [
        GraphEdge(source=ticket.id, target=dep_id)
        for ticket in projection.all_tickets()
        for dep_id in dict.fromkeys(ticket.dependsOn)
        if dep_id != ticket.id and dep_id in known_ids
    ]
    return TicketGraph(nodes=nodes, edges=edges)
