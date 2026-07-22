"""Integration tests for ``GET /api/v1/health`` and its OpenAPI shape.

Supersedes the walking-skeleton ``test_health.py``: health has moved out of the
inline ``app.py`` router into ``api/v1/health.py`` and is enriched to report the
resolved ``projectRoot`` from ``app.state``. Drives the ASGI app in-process via
``httpx.ASGITransport`` (httpx 0.28 removed the ``AsyncClient(app=...)`` shortcut),
pinning the enriched body, the unbound-root case, and that the schema is an
OpenAPI 3 document listing the prefixed health path (carried over from the file
this one replaces).
"""

from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI

import factory_console
from factory_console.app import create_app
from factory_console.domain import Project
from factory_console.file_adapter import FakeFileAdapter

# A real fixture root so the enriched ``projectRoot`` echoes a concrete path. Health
# reads only ``app.state`` and never touches the filesystem, so the seeded project
# and empty ticket list keep the test deterministic and I/O-free.
_PROJECT_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "projects" / "with_run_state"


def _make_fake() -> FakeFileAdapter:
    """Build a minimal in-memory FileAdapter over an empty ticket list."""
    project = Project(
        rootPath=_PROJECT_ROOT,
        ticketsManifestPath=_PROJECT_ROOT / "docs" / "planning" / "tickets.json",
        ticketsDir=_PROJECT_ROOT / "docs" / "planning" / "tickets",
        discoveredAt=datetime(2026, 1, 1),
    )
    return FakeFileAdapter(project=project, tickets=[])


def _make_app() -> FastAPI:
    """Build the real app with the fixture root bound on ``app.state.project_root``."""
    return create_app(
        _make_fake(),
        version=factory_console.__version__,
        project_root=_PROJECT_ROOT,
    )


def _asgi_client(app: FastAPI) -> httpx.AsyncClient:
    """Return an in-process httpx client bound to ``app`` via ASGITransport."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_health_reports_ok_version_and_bound_project_root() -> None:
    async with _asgi_client(_make_app()) as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "version": factory_console.__version__,
        "projectRoot": str(_PROJECT_ROOT),
    }


async def test_health_reports_null_project_root_when_unbound() -> None:
    app = _make_app()
    app.state.project_root = None
    async with _asgi_client(app) as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "version": factory_console.__version__,
        "projectRoot": None,
    }


async def test_openapi_is_v3_and_lists_prefixed_health() -> None:
    async with _asgi_client(_make_app()) as client:
        resp = await client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["openapi"].startswith("3")
    assert "/api/v1/health" in schema["paths"]
