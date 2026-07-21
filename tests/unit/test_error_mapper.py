"""Unit tests for the FastAPI error-mapping handlers.

Pin that :func:`register_error_handlers` renders every ``FactoryConsoleError``
subtype to its declared ``{error: {code, message, details?}}`` envelope and
status, that a ``ticket_id`` ``Path`` pattern violation is re-mapped to the SAME
``invalid_ticket_id`` (400) envelope a deep ``PathTraversal`` yields (NOT 422),
and that an unrelated validation failure stays a ``validation_error`` (422) with a
``details`` list. Deterministic and I/O-free — a bare ``FastAPI`` app with probe
routes, driven in-process by ``TestClient``.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi import Path as PathParam
from fastapi.testclient import TestClient
from pydantic import BaseModel

from factory_console.api.error_handlers import register_error_handlers
from factory_console.domain import TICKET_ID_PATTERN
from factory_console.file_adapter.discovery import ProjectNotFound
from factory_console.file_adapter.manifest import MalformedManifest
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.ticket_md import TicketFileMissing


class _Body(BaseModel):
    """Minimal request body used to trigger a non-ticket-id validation error."""

    count: int


def _client() -> TestClient:
    """Return a TestClient over a bare app wired with the error handlers + probes."""
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/raise/project-not-found")
    def _project_not_found() -> None:
        raise ProjectNotFound(Path("/x"))

    @app.get("/raise/malformed-manifest")
    def _malformed_manifest() -> None:
        raise MalformedManifest(Path("/x/tickets.json"))

    @app.get("/raise/path-traversal")
    def _path_traversal() -> None:
        raise PathTraversal("bad id")

    @app.get("/raise/ticket-file-missing")
    def _ticket_file_missing() -> None:
        raise TicketFileMissing("T99")

    @app.get("/probe/{ticket_id}")
    def _probe_ticket(ticket_id: str = PathParam(..., pattern=TICKET_ID_PATTERN)) -> dict[str, str]:
        return {"ticketId": ticket_id}

    @app.post("/probe-body")
    def _probe_body(body: _Body) -> dict[str, int]:
        return {"count": body.count}

    return TestClient(app)


def test_project_not_found_maps_to_404_envelope() -> None:
    resp = _client().get("/raise/project-not-found")
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "project_not_found"
    assert error["message"]


def test_malformed_manifest_maps_to_500_envelope() -> None:
    resp = _client().get("/raise/malformed-manifest")
    assert resp.status_code == 500
    error = resp.json()["error"]
    assert error["code"] == "malformed_manifest"
    assert error["details"] == {"path": "/x/tickets.json"}


def test_path_traversal_maps_to_400_invalid_ticket_id_envelope() -> None:
    resp = _client().get("/raise/path-traversal")
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "invalid_ticket_id"
    assert error["details"] == {"ticketId": "bad id"}


def test_ticket_file_missing_maps_to_404_envelope() -> None:
    resp = _client().get("/raise/ticket-file-missing")
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "ticket_file_missing"
    assert error["details"] == {"ticketId": "T99"}


def test_invalid_ticket_id_path_param_maps_to_400_not_422() -> None:
    # A space violates TICKET_ID_PATTERN; the Path boundary must yield the SAME
    # invalid_ticket_id envelope a deep PathTraversal would, not a 422.
    resp = _client().get("/probe/bad id")
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "invalid_ticket_id"
    assert error["message"] == "Ticket id must match ^[A-Za-z0-9_.-]+$"
    assert error["details"] == {"ticketId": "bad id"}


def test_unrelated_validation_error_maps_to_422_with_details_list() -> None:
    resp = _client().post("/probe-body", json={"count": "not-an-int"})
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_error"
    assert isinstance(error["details"], list)
    assert error["details"]  # non-empty list of raw validation errors
