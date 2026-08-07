"""Integration tests for ``GET /api/v1/graph``.

Drive an app built with FastAPI's ``TestClient`` over a :class:`FakeFileAdapter`
seeded with a small dependency web that INCLUDES a dangling ``dependsOn`` id (one
with no matching ticket). Pin that the endpoint returns the whole-project
:class:`~factory_console.domain.graph.TicketGraph`: one node per manifest ticket
(each carrying ``runState``), one edge per RESOLVED ``dependsOn`` (source depends
on target), and NO edge referencing an unknown node id — the dangling dep
produces no edge. Also pin the frozen OpenAPI shape the frontend codegen freezes
against.
"""

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.app import create_app
from factory_console.domain import Project, RunState, Ticket
from factory_console.file_adapter import FakeFileAdapter
from factory_console.services.project_selection import SelectionState
from factory_console.store.fake_registry import FakeProjectRegistry

_FAKE_PROJECT = Project(
    rootPath=Path("/factory/demo-project"),
    ticketsManifestPath=Path("/factory/demo-project/docs/planning/tickets.json"),
    ticketsDir=Path("/factory/demo-project/docs/planning/tickets"),
    discoveredAt=datetime(2026, 7, 21, 12, 30, 0),
)


def _make_ticket(ticket_id: str, *, depends_on: list[str] | None = None) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=f"Ticket {ticket_id}",
        status="todo",
        track="backend",
        milestone="MVP",
        dependsOn=depends_on or [],
        filePath=Path(f"/factory/demo-project/docs/planning/tickets/{ticket_id}.md"),
        bodyMarkdown=f"# {ticket_id}",
        bodyHtml=f"<h1>{ticket_id}</h1>",
        raw={"id": ticket_id},
    )


# A small DAG: T-118 depends on T-100; T-125 depends on both; T-131 depends on
# T-125 AND a dangling id (T-999-missing) that resolves to no seeded ticket.
def _tickets() -> list[Ticket]:
    return [
        _make_ticket("T-100"),
        _make_ticket("T-118", depends_on=["T-100"]),
        _make_ticket("T-125", depends_on=["T-100", "T-118"]),
        _make_ticket("T-131", depends_on=["T-125", "T-999-missing"]),
    ]


def _fake_app(
    *,
    project_root: Path = Path("/factory/demo-project"),
    registry: FakeProjectRegistry | None = None,
) -> FastAPI:
    """Build the real app over a FakeFileAdapter seeded with the dependency web."""
    adapter = FakeFileAdapter(
        project=_FAKE_PROJECT,
        tickets=_tickets(),
        run_states={
            "T-100": RunState.ready,
            "T-118": RunState.in_flight,
            "T-125": RunState.in_flight,
            "T-131": RunState.unknown,
        },
    )
    return create_app(
        adapter,
        version="0.0.0",
        project_root=project_root,
        project_registry=registry,
    )


# --------------------------------------------------------------------------- #
# Graph payload shape: nodes + run-state-carrying, resolved edges only
# --------------------------------------------------------------------------- #


def test_graph_returns_nodes_and_resolved_edges() -> None:
    client = TestClient(_fake_app())
    resp = client.get("/api/v1/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"nodes", "edges"}

    # One node per seeded manifest ticket, in manifest order, each with runState.
    node_ids = [node["id"] for node in body["nodes"]]
    assert node_ids == ["T-100", "T-118", "T-125", "T-131"]
    assert all("runState" in node for node in body["nodes"])
    run_state_by_id = {node["id"]: node["runState"] for node in body["nodes"]}
    assert run_state_by_id["T-100"] == "ready"
    assert run_state_by_id["T-118"] == "in-flight"

    # One edge per RESOLVED dependsOn (source depends on target); the dangling
    # T-999-missing produces no edge.
    edges = {(edge["source"], edge["target"]) for edge in body["edges"]}
    assert edges == {
        ("T-118", "T-100"),
        ("T-125", "T-100"),
        ("T-125", "T-118"),
        ("T-131", "T-125"),
    }


def test_graph_omits_edges_to_unknown_nodes() -> None:
    client = TestClient(_fake_app())
    body = client.get("/api/v1/graph").json()
    known_ids = {node["id"] for node in body["nodes"]}
    # No edge references an unknown node id (the dangling dep is excluded).
    for edge in body["edges"]:
        assert edge["source"] in known_ids
        assert edge["target"] in known_ids
    assert "T-999-missing" not in known_ids


# --------------------------------------------------------------------------- #
# Resolution through the v3.0 selection seam (T116)
# --------------------------------------------------------------------------- #


def test_graph_refuses_with_409_when_nothing_is_selected() -> None:
    # No selection means no ticket web to draw. A 200 with an empty graph would read
    # as a measurement of a project ("this one has no tickets") rather than as the
    # console having nothing to measure.
    app = _fake_app()
    app.state.selection = SelectionState(pinned_root=None, registry=None)

    resp = TestClient(app).get("/api/v1/graph")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_project_selected"


def test_graph_refuses_with_409_when_the_selected_path_is_gone(tmp_path: Path) -> None:
    gone = tmp_path / "gone"
    gone.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(gone)
    app = _fake_app(project_root=tmp_path / "pinned", registry=registry)
    app.state.selection.select(row.id)
    gone.rmdir()

    resp = TestClient(app).get("/api/v1/graph")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "selected_project_unavailable"


# --------------------------------------------------------------------------- #
# Frozen OpenAPI shape (what the frontend codegen freezes against)
# --------------------------------------------------------------------------- #


def test_openapi_publishes_graph_path_and_schema() -> None:
    client = TestClient(_fake_app())
    schema = client.get("/api/v1/openapi.json").json()
    assert "/api/v1/graph" in schema["paths"]
    assert "TicketGraph" in schema["components"]["schemas"]
    ref = schema["paths"]["/api/v1/graph"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    assert ref.endswith("/TicketGraph")
