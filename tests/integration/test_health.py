"""Integration tests for the walking-skeleton health probe and OpenAPI schema.

Drive the ASGI app in-process via ``httpx.ASGITransport`` (httpx 0.28 removed the
``AsyncClient(app=...)`` shortcut). Pin the exact ``/api/v1/health`` body and that
the schema is an OpenAPI 3 document listing the prefixed health path.
"""

from datetime import datetime
from pathlib import Path

import httpx

import factory_console
from factory_console.app import create_app
from factory_console.domain import Project
from factory_console.file_adapter import FakeFileAdapter


def _make_fake() -> FakeFileAdapter:
    """Build a minimal in-memory FileAdapter over an empty ticket list."""
    project = Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        discoveredAt=datetime(2026, 1, 1),
    )
    return FakeFileAdapter(project=project, tickets=[])


def _asgi_client() -> httpx.AsyncClient:
    """Return an in-process httpx client bound to a fresh app via ASGITransport."""
    app = create_app(
        _make_fake(),
        version=factory_console.__version__,
        project_root=Path("/tmp/fake-root"),
    )
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_health_returns_ok_shape() -> None:
    async with _asgi_client() as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "version": factory_console.__version__,
        "projectRoot": None,
    }


async def test_openapi_is_v3_and_lists_prefixed_health() -> None:
    async with _asgi_client() as client:
        resp = await client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["openapi"].startswith("3")
    assert "/api/v1/health" in schema["paths"]
