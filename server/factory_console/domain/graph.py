"""Whole-project dependency-graph domain models — nodes, edges, and the DAG.

A :class:`TicketGraph` is the whole-project projection the ``/graph`` route
renders: every ticket a :class:`GraphNode` (carrying the run-state the shared
:class:`~factory_console.file_adapter.projection.TicketProjection` already
resolved) and every RESOLVED ``dependsOn`` a :class:`GraphEdge`. It is a
different shape from the per-ticket
:class:`~factory_console.domain.deps.DepNeighborhood` the deps view serves —
whole-graph rather than one ticket's neighbourhood.

The nodes+edges payload is built by ``file_adapter/graph.py``'s ``build_graph``
from that shared projection, so node run-state can never drift from the list or
deps views. Like :class:`~factory_console.domain.search.SearchHit`, these models
are imported by full path from their consumers and deliberately NOT re-exported
from ``domain/__init__`` so that aggregation file stays collision-free across the
parallel v1 tickets.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from factory_console.domain.run_state import RunState
from factory_console.domain.ticket import TicketId


class GraphNode(BaseModel):
    """One ticket as a graph node, coloured by its resolved run-state.

    Carries the same identity/classification fields as the list-view summary —
    ``id``, ``title``, ``status``, ``track``, ``milestone`` — plus the
    ``runState`` the shared projection already resolved, so a node's colour
    matches the list and deps views. Frozen and ``extra='forbid'`` like the other
    domain models.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: TicketId
    title: str
    status: str
    track: str | None = None
    milestone: str | None = None
    runState: RunState


class GraphEdge(BaseModel):
    """A directed dependency edge: ``source`` depends on ``target``.

    Only edges whose ``target`` resolves to a known node are emitted (dangling
    ``dependsOn`` ids are intentionally NOT edges, consistent with
    :attr:`~factory_console.domain.deps.DepNeighborhood.unresolvedDeps`); self-loops
    are dropped. Frozen and ``extra='forbid'`` like the other domain models.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    target: str


class TicketGraph(BaseModel):
    """The whole-project dependency DAG: run-state-coloured nodes and edges.

    ``nodes`` are the ticket summaries in list order; ``edges`` are the resolved,
    self-loop-free, de-duplicated ``dependsOn`` relations. The frontend maps this
    payload to Cytoscape element format (not this model's concern). Frozen and
    ``extra='forbid'`` like the other domain models.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
