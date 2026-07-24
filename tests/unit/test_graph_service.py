"""Unit tests for :class:`GraphService` over the in-memory :class:`FakeFileAdapter`.

Pin the one behavior the HTTP handler delegates to: returning the whole-project
:class:`~factory_console.domain.graph.TicketGraph` the adapter builds, verbatim.
The service is a thin orchestrator, so the test asserts identity with
``adapter.get_graph`` (same object) rather than re-deriving the graph shape —
:mod:`tests.unit.test_graph` already covers the projection's node/edge semantics.
Deterministic and I/O-free: the fake answers every call from seeded in-memory
data.
"""

from datetime import datetime
from pathlib import Path

from factory_console.domain import Project, Ticket
from factory_console.file_adapter import FakeFileAdapter
from factory_console.services.graph_service import GraphService


def _make_project() -> Project:
    return Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        roadmapPath=Path("/proj/ROADMAP.md"),
        runStateDir=Path("/proj/.factory/run-state"),
        discoveredAt=datetime(2026, 7, 21, 12, 0, 0),
    )


def _make_ticket(ticket_id: str, *, depends_on: list[str] | None = None) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=f"Ticket {ticket_id}",
        status="todo",
        track="backend",
        milestone="MVP",
        dependsOn=depends_on or [],
        filePath=Path(f"/proj/docs/planning/tickets/{ticket_id}.md"),
        bodyMarkdown=f"# {ticket_id}",
        bodyHtml=f"<h1>{ticket_id}</h1>",
        raw={"id": ticket_id},
    )


# A small DAG: T-118 depends on T-100; T-125 depends on both.
def _tickets() -> list[Ticket]:
    return [
        _make_ticket("T-100"),
        _make_ticket("T-118", depends_on=["T-100"]),
        _make_ticket("T-125", depends_on=["T-100", "T-118"]),
    ]


def _service() -> tuple[GraphService, FakeFileAdapter, Project]:
    project = _make_project()
    fake = FakeFileAdapter(project=project, tickets=_tickets())
    return GraphService(fake), fake, project


def test_get_graph_delegates_to_adapter() -> None:
    service, fake, project = _service()
    # The service is a thin orchestrator: it returns exactly what the adapter
    # builds for the same project, with no re-shaping.
    assert service.get_graph(project) == fake.get_graph(project)


def test_get_graph_returns_nodes_and_edges() -> None:
    service, _fake, project = _service()
    graph = service.get_graph(project)
    assert [node.id for node in graph.nodes] == ["T-100", "T-118", "T-125"]
    assert {(edge.source, edge.target) for edge in graph.edges} == {
        ("T-118", "T-100"),
        ("T-125", "T-100"),
        ("T-125", "T-118"),
    }
