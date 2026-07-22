"""Integration tests for ``GET /api/v1/roadmap`` — the MVP presence envelope.

Drives an app built over the filesystem-backed :class:`RealFileAdapter` against the
checked-in fixture projects with FastAPI's ``TestClient``, asserting BOTH branches
of the presence contract through the HTTP edge: a project that ships a
``ROADMAP.md`` returns ``{present: true, path}`` and one without returns
``{present: false}``. The absent case points at the ``malformed`` fixture — the
codebase's canonical no-roadmap project (its manifest exists so ``load_project``
succeeds, but no ``ROADMAP.md`` resolves, so ``roadmapPath`` is ``None``; see
``test_real_file_adapter.py``) — because both the ``minimal`` and ``with_run_state``
fixtures ship a roadmap. Also pins the ``/api/v1/roadmap`` path in the OpenAPI doc.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.app import create_app
from factory_console.file_adapter.real import RealFileAdapter

PROJECTS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "projects"
WITH_RUN_STATE = PROJECTS_DIR / "with_run_state"
MALFORMED = PROJECTS_DIR / "malformed"


def _make_app(project_root: Path) -> FastAPI:
    """Build the real app over a filesystem-backed adapter rooted at ``project_root``."""
    return create_app(RealFileAdapter(), version="0.0.0", project_root=project_root)


def test_roadmap_reports_present_with_path_when_project_ships_one() -> None:
    client = TestClient(_make_app(WITH_RUN_STATE))
    resp = client.get("/api/v1/roadmap")
    assert resp.status_code == 200
    body = resp.json()
    assert body["present"] is True
    assert body["path"].endswith("ROADMAP.md")


def test_roadmap_reports_absent_when_project_has_no_roadmap() -> None:
    client = TestClient(_make_app(MALFORMED))
    resp = client.get("/api/v1/roadmap")
    assert resp.status_code == 200
    assert resp.json() == {"present": False}


def test_openapi_publishes_roadmap_path() -> None:
    client = TestClient(_make_app(WITH_RUN_STATE))
    resp = client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    assert "/api/v1/roadmap" in resp.json()["paths"]
