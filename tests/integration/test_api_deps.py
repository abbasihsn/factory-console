"""Integration tests for ``GET /api/v1/tickets/{ticket_id}/deps``.

Drive apps built with FastAPI's ``TestClient`` over both adapters: the
filesystem-backed :class:`RealFileAdapter` over the checked-in ``with_run_state``
fixture for the happy neighborhood shape, the dangling-edge ``unresolvedDeps``
case, and the ``ticket_not_found`` 404; and a seeded :class:`FakeFileAdapter` for
the ``invalid_ticket_id`` 400 (rejected at the ``Path`` boundary) and the frozen
OpenAPI shape. Fixture facts (dep edges, dependents, the dangling
``CAD-207-nonexistent`` reference) match ``tests/integration/test_real_file_adapter.py``.
"""

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.app import create_app
from factory_console.domain import Project, RunState, Ticket
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.real import RealFileAdapter

# Locate the checked-in fixture project the same way as test_api_tickets.py.
PROJECTS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "projects"
WITH_RUN_STATE = PROJECTS_DIR / "with_run_state"

_FAKE_PROJECT = Project(
    rootPath=Path("/factory/demo-project"),
    ticketsManifestPath=Path("/factory/demo-project/docs/planning/tickets.json"),
    ticketsDir=Path("/factory/demo-project/docs/planning/tickets"),
    discoveredAt=datetime(2026, 7, 21, 12, 30, 0),
)


def _fake_ticket(ticket_id: str) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=f"Ticket {ticket_id}",
        status="todo",
        track="backend",
        milestone="MVP",
        filePath=Path(f"/factory/demo-project/docs/planning/tickets/{ticket_id}.md"),
        bodyMarkdown=f"# {ticket_id}",
        bodyHtml=f"<h1>{ticket_id}</h1>",
        raw={"id": ticket_id},
    )


def _fake_app() -> FastAPI:
    """Build the real app over a FakeFileAdapter (enough to reach the Path boundary)."""
    adapter = FakeFileAdapter(
        project=_FAKE_PROJECT,
        tickets=[_fake_ticket("FAKE-1")],
        run_states={"FAKE-1": RunState.ready},
    )
    return create_app(adapter, version="0.0.0", project_root=Path("/factory/demo-project"))


def _real_app() -> FastAPI:
    """Build the real app over the filesystem-backed adapter and the fixture project."""
    return create_app(RealFileAdapter(), version="0.0.0", project_root=WITH_RUN_STATE)


def _ids(items: list[dict]) -> list[str]:
    return [item["id"] for item in items]


# --------------------------------------------------------------------------- #
# Happy neighborhood shape (real adapter)
# --------------------------------------------------------------------------- #


def test_deps_returns_resolved_deps_and_dependents() -> None:
    client = TestClient(_real_app())
    resp = client.get("/api/v1/tickets/CAD-125/deps")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticket"]["id"] == "CAD-125"
    assert _ids(body["directDeps"]) == ["CAD-100", "CAD-118"]
    assert _ids(body["directDependents"]) == ["CAD-131", "CAD-140"]
    assert body["unresolvedDeps"] == []


def test_deps_surfaces_dangling_edge_in_unresolved() -> None:
    client = TestClient(_real_app())
    resp = client.get("/api/v1/tickets/CAD-131/deps")
    assert resp.status_code == 200
    body = resp.json()
    # CAD-131 declares CAD-125 (resolves) + CAD-207-nonexistent (dangling).
    assert _ids(body["directDeps"]) == ["CAD-125"]
    assert body["unresolvedDeps"] == ["CAD-207-nonexistent"]
    assert body["directDependents"] == []


# --------------------------------------------------------------------------- #
# Error envelopes
# --------------------------------------------------------------------------- #


def test_deps_unknown_id_maps_to_ticket_not_found_404() -> None:
    client = TestClient(_real_app())
    resp = client.get("/api/v1/tickets/CAD-999/deps")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ticket_not_found"


def test_deps_invalid_id_rejected_at_path_boundary_as_400() -> None:
    # A '$' is outside TICKET_ID_PATTERN, so the Path validator rejects it before
    # the handler runs — the adapter is never reached (that would be a 404).
    client = TestClient(_fake_app())
    resp = client.get("/api/v1/tickets/bad$id/deps")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_ticket_id"


# --------------------------------------------------------------------------- #
# Frozen OpenAPI shape (what the frontend codegen freezes against)
# --------------------------------------------------------------------------- #


def test_openapi_publishes_deps_path_and_schema() -> None:
    client = TestClient(_fake_app())
    schema = client.get("/api/v1/openapi.json").json()
    assert "/api/v1/tickets/{ticket_id}/deps" in schema["paths"]
    assert "DepNeighborhood" in schema["components"]["schemas"]
    ref = schema["paths"]["/api/v1/tickets/{ticket_id}/deps"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    assert ref.endswith("/DepNeighborhood")
