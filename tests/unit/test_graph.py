"""Unit tests for the pure ``build_graph`` whole-project DAG projection.

These pin the graph's node and edge semantics directly on a constructed
:class:`~factory_console.file_adapter.projection.TicketProjection` (the same
projection both adapters share), independent of any filesystem: one node per
ticket carrying the projection-resolved ``runState``, and edges that are
RESOLVED-ONLY, self-loop-free, and per-ticket de-duplicated. Deterministic and
I/O-free — pydantic + stdlib only.
"""

from pathlib import Path

from factory_console.domain import RunState, Ticket
from factory_console.domain.graph import GraphEdge, GraphNode, TicketGraph
from factory_console.file_adapter.graph import build_graph
from factory_console.file_adapter.projection import TicketProjection


def _make_ticket(ticket_id: str, *, depends_on: list[str] | None = None) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=f"Ticket {ticket_id}",
        status="open",
        track="file-adapter",
        milestone="MVP",
        dependsOn=depends_on or [],
        filePath=Path(f"/proj/docs/planning/tickets/{ticket_id}.md"),
        bodyMarkdown=f"# {ticket_id}",
        bodyHtml=f"<h1>{ticket_id}</h1>",
        raw={"id": ticket_id},
    )


def _projection(
    tickets: list[Ticket], run_states: dict[str, RunState] | None = None
) -> TicketProjection:
    states = run_states or {}
    return TicketProjection(
        tickets,
        run_state_for=lambda ticket_id: states.get(ticket_id, RunState.unknown),
    )


def _edge_pairs(graph: TicketGraph) -> list[tuple[str, str]]:
    return [(edge.source, edge.target) for edge in graph.edges]


def test_build_graph_returns_a_ticket_graph() -> None:
    graph = build_graph(_projection([_make_ticket("T-A")]))
    assert isinstance(graph, TicketGraph)
    assert all(isinstance(node, GraphNode) for node in graph.nodes)
    assert all(isinstance(edge, GraphEdge) for edge in graph.edges)


def test_node_count_equals_ticket_count_in_list_order() -> None:
    tickets = [_make_ticket("T-A"), _make_ticket("T-B", depends_on=["T-A"]), _make_ticket("T-C")]
    graph = build_graph(_projection(tickets))
    assert len(graph.nodes) == len(tickets)
    assert [node.id for node in graph.nodes] == ["T-A", "T-B", "T-C"]


def test_each_node_carries_run_state_from_the_summary() -> None:
    tickets = [_make_ticket("T-A"), _make_ticket("T-B"), _make_ticket("T-C")]
    run_states = {"T-A": RunState.merged, "T-B": RunState.in_flight}
    graph = build_graph(_projection(tickets, run_states))
    by_id = {node.id: node for node in graph.nodes}
    assert by_id["T-A"].runState is RunState.merged
    assert by_id["T-B"].runState is RunState.in_flight
    # T-C has no seeded run-state -> unknown, straight from the projection.
    assert by_id["T-C"].runState is RunState.unknown
    # Other carried-over fields come from the summary too.
    assert by_id["T-B"].title == "Ticket T-B"
    assert by_id["T-B"].status == "open"
    assert by_id["T-B"].track == "file-adapter"
    assert by_id["T-B"].milestone == "MVP"


def test_edges_only_connect_known_nodes() -> None:
    tickets = [_make_ticket("T-A"), _make_ticket("T-B", depends_on=["T-A"])]
    graph = build_graph(_projection(tickets))
    node_ids = {node.id for node in graph.nodes}
    assert _edge_pairs(graph) == [("T-B", "T-A")]
    for edge in graph.edges:
        assert edge.source in node_ids
        assert edge.target in node_ids


def test_dangling_depends_on_id_produces_no_edge() -> None:
    # GHOST-1 has no ticket, so its edge is intentionally omitted (like unresolvedDeps).
    tickets = [_make_ticket("T-A"), _make_ticket("T-B", depends_on=["T-A", "GHOST-1"])]
    graph = build_graph(_projection(tickets))
    assert _edge_pairs(graph) == [("T-B", "T-A")]
    assert all(edge.target != "GHOST-1" for edge in graph.edges)


def test_self_loop_edge_is_dropped() -> None:
    # A ticket that lists itself yields no source == target edge.
    tickets = [_make_ticket("T-S", depends_on=["T-S"])]
    graph = build_graph(_projection(tickets))
    assert graph.edges == []


def test_duplicate_depends_on_id_collapses_to_one_edge() -> None:
    tickets = [_make_ticket("T-A"), _make_ticket("T-B", depends_on=["T-A", "T-A"])]
    graph = build_graph(_projection(tickets))
    assert _edge_pairs(graph) == [("T-B", "T-A")]


def test_empty_projection_yields_empty_graph() -> None:
    graph = build_graph(_projection([]))
    assert graph.nodes == []
    assert graph.edges == []
