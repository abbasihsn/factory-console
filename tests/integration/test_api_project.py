"""Integration tests for ``GET /api/v1/project`` and its frozen OpenAPI shape.

Drive an app built over a :class:`FakeFileAdapter` with FastAPI's ``TestClient``
and pin the first real endpoint: the discovered :class:`Project` is returned as
JSON, the ``/api/v1/project`` path is published in the v1 OpenAPI document with its
200 response referencing the ``Project`` schema (what the frontend codegen freezes
against), and a ``ProjectNotFound`` raised by the adapter maps to the 404 envelope
through T20's registered domain-error handler. Deterministic and I/O-free — the
fake is seeded with an empty ticket list; no filesystem is touched.
"""

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.app import create_app
from factory_console.domain import Project
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.discovery import ProjectNotFound

# Distinctive, resolved paths so the body assertions are meaningful (roadmapPath and
# runStateDir stay unseeded, i.e. ``None``, exercising the optional-path serialization).
_PROJECT = Project(
    rootPath=Path("/factory/demo-project"),
    ticketsManifestPath=Path("/factory/demo-project/docs/planning/tickets.json"),
    ticketsDir=Path("/factory/demo-project/docs/planning/tickets"),
    discoveredAt=datetime(2026, 7, 21, 12, 30, 0),
)


class _ProjectNotFoundAdapter(FakeFileAdapter):
    """A FakeFileAdapter whose ``load_project`` always raises ``ProjectNotFound``.

    Proves the endpoint's error path wires through T20's domain-error mapper without
    the handler doing any error handling of its own.
    """

    def load_project(self, root: Path) -> Project:
        """Raise ``ProjectNotFound`` for the discovered ``root`` instead of loading."""
        raise ProjectNotFound(root)


def _make_app(adapter: FakeFileAdapter | None = None) -> FastAPI:
    """Build the real app over a project-seeded FakeFileAdapter (or the given one)."""
    return create_app(
        adapter or FakeFileAdapter(project=_PROJECT, tickets=[]),
        version="0.0.0",
        project_root=Path("/factory/demo-project"),
    )


def test_get_project_returns_the_discovered_project_shape() -> None:
    client = TestClient(_make_app())
    resp = client.get("/api/v1/project")
    assert resp.status_code == 200
    body = resp.json()
    assert body["rootPath"] == "/factory/demo-project"
    assert body["ticketsManifestPath"] == "/factory/demo-project/docs/planning/tickets.json"
    assert body["ticketsDir"] == "/factory/demo-project/docs/planning/tickets"
    assert body["roadmapPath"] is None
    assert body["runStateDir"] is None
    assert body["discoveredAt"] == "2026-07-21T12:30:00"


def test_openapi_publishes_project_path_referencing_the_project_schema() -> None:
    client = TestClient(_make_app())
    resp = client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "/api/v1/project" in schema["paths"]
    assert "Project" in schema["components"]["schemas"]
    ref = schema["paths"]["/api/v1/project"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    assert ref.endswith("/Project")


def test_get_project_maps_project_not_found_to_404_envelope() -> None:
    client = TestClient(_make_app(_ProjectNotFoundAdapter(project=_PROJECT, tickets=[])))
    resp = client.get("/api/v1/project")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "project_not_found"
