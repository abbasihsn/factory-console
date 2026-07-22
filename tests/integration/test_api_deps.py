"""Integration tests for ``GET /api/v1/tickets/{ticket_id}/deps``.

Drive apps built with FastAPI's ``TestClient``: the filesystem-backed
:class:`RealFileAdapter` over the checked-in ``with_run_state`` fixture for the
neighborhood shapes (resolved deps + dependents, and a dangling reference that
surfaces in ``unresolvedDeps``) and the 404, plus a seeded
:class:`FakeFileAdapter` for the ``invalid_ticket_id`` 400 rejected at the
``Path`` boundary. Pin that the endpoint returns a :class:`DepNeighborhood` with
``directDeps`` in ``dependsOn`` order, ``directDependents`` in manifest order,
and ``unresolvedDeps`` for ids with no matching ticket.
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


def _fake_app() -> FastAPI:
    """Build the real app over a FakeFileAdapter (used only for the invalid-id path)."""
    tickets = [
        Ticket(
            id="FAKE-1",
            title="Alpha widget",
            status="todo",
            track="backend",
            milestone="MVP",
            filePath=Path("/factory/demo-project/docs/planning/tickets/FAKE-1.md"),
            bodyMarkdown="# Alpha widget",
            bodyHtml="<h1>Alpha widget</h1>",
            raw={"id": "FAKE-1"},
        )
    ]
    adapter = FakeFileAdapter(
        project=_FAKE_PROJECT,
        tickets=tickets,
        run_states={"FAKE-1": RunState.ready},
    )
    return create_app(adapter, version="0.0.0", project_root=Path("/factory/demo-project"))


def _real_app() -> FastAPI:
    """Build the real app over the filesystem-backed adapter and the fixture project."""
    return create_app(RealFileAdapter(), version="0.0.0", project_root=WITH_RUN_STATE)


def _ids(items: list[dict]) -> list[str]:
    return [item["id"] for item in items]


# --------------------------------------------------------------------------- #
# Happy shape: resolved deps + dependents (real adapter)
# --------------------------------------------------------------------------- #


def test_deps_returns_resolved_deps_and_dependents() -> None:
    client = TestClient(_real_app())
    resp = client.get("/api/v1/tickets/CAD-125/deps")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"ticket", "directDeps", "directDependents", "unresolvedDeps"}
    assert body["ticket"]["id"] == "CAD-125"
    # directDeps follow dependsOn order; directDependents follow manifest order.
    assert _ids(body["directDeps"]) == ["CAD-100", "CAD-118"]
    assert _ids(body["directDependents"]) == ["CAD-131", "CAD-140"]
    assert body["unresolvedDeps"] == []


# --------------------------------------------------------------------------- #
# Unresolved (dangling) dep id surfaced (real adapter)
# --------------------------------------------------------------------------- #


def test_deps_surfaces_unresolved_dep_id() -> None:
    client = TestClient(_real_app())
    resp = client.get("/api/v1/tickets/CAD-131/deps")
    assert resp.status_code == 200
    body = resp.json()
    assert _ids(body["directDeps"]) == ["CAD-125"]
    assert body["unresolvedDeps"] == ["CAD-207-nonexistent"]
    assert body["directDependents"] == []


# --------------------------------------------------------------------------- #
# No-deps ticket keeps its dependents (real adapter)
# --------------------------------------------------------------------------- #


def test_deps_no_deps_ticket_keeps_dependents() -> None:
    client = TestClient(_real_app())
    resp = client.get("/api/v1/tickets/CAD-100/deps")
    assert resp.status_code == 200
    body = resp.json()
    assert body["directDeps"] == []
    assert body["unresolvedDeps"] == []
    assert _ids(body["directDependents"]) == ["CAD-118", "CAD-125"]


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
