"""Integration tests for the real ``create_app`` application factory.

Drive an app built over a :class:`FakeFileAdapter` with FastAPI's ``TestClient``
and pin the cross-cutting seams T20 installs: the ``/api/v1/openapi.json`` schema,
the ``Depends(get_file_adapter)`` DI wiring, the domain/validation exception
handlers reachable through probe routes mounted on the created app, and exactly
one ``factory_console.access`` log line per request. Deterministic and I/O-free —
the fake is seeded with an empty ticket list; no filesystem is touched.
"""

import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi import Path as PathParam
from fastapi.testclient import TestClient
from pydantic import BaseModel

from factory_console.api.deps import get_file_adapter
from factory_console.app import create_app
from factory_console.domain import TICKET_ID_PATTERN, Project
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.discovery import ProjectNotFound


class _Body(BaseModel):
    """Minimal request body used to trigger a non-ticket-id validation error."""

    count: int


def _make_fake() -> FakeFileAdapter:
    """Build a minimal FakeFileAdapter over an empty ticket list."""
    project = Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        discoveredAt=datetime(2026, 1, 1),
    )
    return FakeFileAdapter(project=project, tickets=[])


def _make_app(fake: FakeFileAdapter | None = None) -> FastAPI:
    """Build the real app plus probe routes exercising each cross-cutting seam."""
    app = create_app(
        fake or _make_fake(),
        version="0.0.0",
        project_root=Path("/tmp/fake-root"),
    )

    @app.get("/probe")
    def _probe() -> None:
        raise ProjectNotFound(Path("/x"))

    @app.post("/probe-body")
    def _probe_body(body: _Body) -> dict[str, int]:
        return {"count": body.count}

    @app.get("/probe/{ticket_id}")
    def _probe_ticket(ticket_id: str = PathParam(..., pattern=TICKET_ID_PATTERN)) -> dict[str, str]:
        return {"ticketId": ticket_id}

    return app


def test_openapi_returns_valid_v3_document() -> None:
    client = TestClient(_make_app())
    resp = client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["openapi"].startswith("3")
    assert "/api/v1/health" in schema["paths"]


def test_get_file_adapter_returns_the_adapter_bound_by_create_app() -> None:
    # create_app stashes the adapter on app.state; the DI provider reads it back so
    # handlers get the exact instance without importing a concrete adapter.
    fake = _make_fake()
    app = _make_app(fake)
    request = SimpleNamespace(app=app)
    assert get_file_adapter(request) is fake  # type: ignore[arg-type]


def test_unhandled_project_not_found_maps_to_404_envelope() -> None:
    client = TestClient(_make_app())
    resp = client.get("/probe")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "project_not_found"


def test_request_validation_error_maps_to_422() -> None:
    client = TestClient(_make_app())
    resp = client.post("/probe-body", json={})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_ticket_id_pattern_violation_maps_to_400_invalid_ticket_id() -> None:
    client = TestClient(_make_app())
    resp = client.get("/probe/bad id")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_ticket_id"


def test_exactly_one_access_log_line_per_request(caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(_make_app())
    caplog.set_level(logging.INFO, logger="factory_console.access")
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    records = [record for record in caplog.records if record.name == "factory_console.access"]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "GET" in message
    assert "/api/v1/health" in message
    assert "200" in message


def test_get_file_adapter_raises_when_unbound() -> None:
    # Directly exercise the DI seam's guard: an app.state without a bound adapter
    # is a wiring bug, so the provider must fail loudly rather than return None.
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(RuntimeError, match="file_adapter"):
        get_file_adapter(request)  # type: ignore[arg-type]
