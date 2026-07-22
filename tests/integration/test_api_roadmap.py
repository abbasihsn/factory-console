"""Integration tests for the presence-only ``GET /api/v1/roadmap`` endpoint.

Drive apps built with FastAPI's ``TestClient`` over both adapters: the
filesystem-backed :class:`RealFileAdapter` over the checked-in ``with_run_state``
fixture (which ships a root ``ROADMAP.md``) pins the ``{present: true, path}``
branch with a real resolved path, and a seeded :class:`FakeFileAdapter` with no
roadmap pins the ``{present: false}`` branch cleanly. Also pins the frozen OpenAPI
shape (the path the frontend codegen freezes against).

Note: the ``minimal`` fixture is NOT used for the absent branch — it ships a
``ROADMAP.md`` too, so ``RealFileAdapter`` over it would resolve a path and return
``present: true``. The genuinely roadmap-less real fixture is ``malformed`` (see
``test_real_file_adapter.py``); the fake adapter gives the same absent result here
without touching the filesystem.
"""

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.app import create_app
from factory_console.domain import Project
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.real import RealFileAdapter

# Locate the checked-in fixture project the same way as the sibling integration tests.
PROJECTS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "projects"
WITH_RUN_STATE = PROJECTS_DIR / "with_run_state"

_FAKE_PROJECT = Project(
    rootPath=Path("/factory/demo-project"),
    ticketsManifestPath=Path("/factory/demo-project/docs/planning/tickets.json"),
    ticketsDir=Path("/factory/demo-project/docs/planning/tickets"),
    discoveredAt=datetime(2026, 7, 21, 12, 30, 0),
)


def _real_app() -> FastAPI:
    """Build the real app over the filesystem-backed adapter and the with_run_state fixture."""
    return create_app(RealFileAdapter(), version="0.0.0", project_root=WITH_RUN_STATE)


def _absent_app() -> FastAPI:
    """Build the real app over a FakeFileAdapter whose project has no roadmap.

    The endpoint decides presence from ``project.roadmapPath`` (it never calls
    ``adapter.get_roadmap``), and ``_FAKE_PROJECT`` leaves ``roadmapPath`` unset, so
    this pins the ``{present: false}`` branch.
    """
    adapter = FakeFileAdapter(project=_FAKE_PROJECT, tickets=[])
    return create_app(adapter, version="0.0.0", project_root=_FAKE_PROJECT.rootPath)


def test_roadmap_present_returns_true_and_resolved_path() -> None:
    client = TestClient(_real_app())
    resp = client.get("/api/v1/roadmap")
    assert resp.status_code == 200
    body = resp.json()
    assert body["present"] is True
    assert body["path"].endswith("ROADMAP.md")


def test_roadmap_absent_returns_present_false() -> None:
    client = TestClient(_absent_app())
    resp = client.get("/api/v1/roadmap")
    assert resp.status_code == 200
    assert resp.json() == {"present": False}


def test_openapi_publishes_roadmap_path() -> None:
    client = TestClient(_absent_app())
    resp = client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    assert "/api/v1/roadmap" in resp.json()["paths"]
