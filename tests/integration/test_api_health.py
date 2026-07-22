"""Integration tests for the relocated + enriched ``GET /api/v1/health`` probe.

The health handler moved out of ``app.py`` into ``api/v1/health.py`` (T24) and now
reports the resolved ``projectRoot`` bound on ``app.state`` at boot instead of a
hard-coded ``null``. Drive an app built over a :class:`FakeFileAdapter` with
FastAPI's ``TestClient`` and pin: the enriched body over the bound root
``create_app`` always binds, and that the schema still publishes the prefixed
``/api/v1/health`` path (folded in from the retired ``test_health.py``).
"""

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import factory_console
from factory_console.app import create_app
from factory_console.domain import Project
from factory_console.file_adapter import FakeFileAdapter

_PROJECT = Project(
    rootPath=Path("/factory/demo-project"),
    ticketsManifestPath=Path("/factory/demo-project/docs/planning/tickets.json"),
    ticketsDir=Path("/factory/demo-project/docs/planning/tickets"),
    discoveredAt=datetime(2026, 7, 21, 12, 30, 0),
)
_ROOT = Path("/factory/demo-project")


def _make_app() -> FastAPI:
    """Build the real app over an empty-ticket FakeFileAdapter bound to ``_ROOT``."""
    return create_app(
        FakeFileAdapter(project=_PROJECT, tickets=[]),
        version=factory_console.__version__,
        project_root=_ROOT,
    )


def test_health_reports_ok_version_and_bound_project_root() -> None:
    client = TestClient(_make_app())
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "version": factory_console.__version__,
        "projectRoot": str(_ROOT),
    }


def test_openapi_publishes_prefixed_health_path() -> None:
    client = TestClient(_make_app())
    resp = client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["openapi"].startswith("3")
    assert "/api/v1/health" in schema["paths"]
