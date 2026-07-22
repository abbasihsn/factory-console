"""Integration tests for ``GET /api/v1/tickets`` and ``/api/v1/tickets/{ticket_id}``.

Drive apps built with FastAPI's ``TestClient`` over both adapters: a seeded
:class:`FakeFileAdapter` for a controlled happy path, the error envelopes, and
the frozen OpenAPI shape; and the filesystem-backed :class:`RealFileAdapter` over
the checked-in ``with_run_state`` fixture (6 tickets spanning every run-state) for
the fixture-driven list/filter/detail assertions. Pin that filters return the
expected id subset, that an unknown id maps to the ``ticket_not_found`` 404
envelope, and that an invalid id is rejected at the ``Path`` boundary as the
``invalid_ticket_id`` 400 envelope without reaching the adapter.
"""

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.app import create_app
from factory_console.domain import Project, RunState, Ticket
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.real import RealFileAdapter

# Locate the checked-in fixture project the same way as test_real_file_adapter.py.
PROJECTS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "projects"
WITH_RUN_STATE = PROJECTS_DIR / "with_run_state"

_FAKE_PROJECT = Project(
    rootPath=Path("/factory/demo-project"),
    ticketsManifestPath=Path("/factory/demo-project/docs/planning/tickets.json"),
    ticketsDir=Path("/factory/demo-project/docs/planning/tickets"),
    discoveredAt=datetime(2026, 7, 21, 12, 30, 0),
)


def _fake_ticket(ticket_id: str, *, title: str, status: str, track: str, milestone: str) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=title,
        status=status,
        track=track,
        milestone=milestone,
        filePath=Path(f"/factory/demo-project/docs/planning/tickets/{ticket_id}.md"),
        bodyMarkdown=f"# {title}",
        bodyHtml=f"<h1>{title}</h1>",
        raw={"id": ticket_id},
    )


def _fake_app() -> FastAPI:
    """Build the real app over a FakeFileAdapter seeded with two distinctive tickets."""
    tickets = [
        _fake_ticket(
            "FAKE-1", title="Alpha widget", status="todo", track="backend", milestone="MVP"
        ),
        _fake_ticket("FAKE-2", title="Beta gadget", status="done", track="api", milestone="v1"),
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
# Controlled happy path + envelope shape (fake adapter)
# --------------------------------------------------------------------------- #


def test_list_returns_items_and_total_envelope() -> None:
    client = TestClient(_fake_app())
    resp = client.get("/api/v1/tickets")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "total"}
    assert body["total"] == 2
    assert _ids(body["items"]) == ["FAKE-1", "FAKE-2"]
    # Run-state is carried on the summary (seeded map) and serializes as its value.
    first = body["items"][0]
    assert first["runState"] == "ready"


# --------------------------------------------------------------------------- #
# Fixture-driven list + filters (real adapter)
# --------------------------------------------------------------------------- #


def test_real_list_returns_all_six_tickets_with_run_state() -> None:
    client = TestClient(_real_app())
    resp = client.get("/api/v1/tickets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 6
    assert len(body["items"]) == 6
    run_states = {item["id"]: item["runState"] for item in body["items"]}
    assert run_states["CAD-100"] == "merged"
    assert run_states["CAD-118"] == "ready"


def test_real_filter_by_status_selects_todo_subset() -> None:
    client = TestClient(_real_app())
    body = client.get("/api/v1/tickets", params={"status": "todo"}).json()
    assert _ids(body["items"]) == ["CAD-131", "CAD-140", "CAD-152"]
    assert body["total"] == 3


def test_real_filter_by_track_selects_backend_subset() -> None:
    client = TestClient(_real_app())
    body = client.get("/api/v1/tickets", params={"track": "backend"}).json()
    assert _ids(body["items"]) == ["CAD-118", "CAD-152"]


def test_real_filter_by_milestone_selects_mvp_subset() -> None:
    client = TestClient(_real_app())
    body = client.get("/api/v1/tickets", params={"milestone": "MVP"}).json()
    assert _ids(body["items"]) == ["CAD-100", "CAD-118", "CAD-125"]


def test_real_search_q_is_case_insensitive_over_title() -> None:
    client = TestClient(_real_app())
    # "Streak computation service" (CAD-118) — lowercase needle, mixed-case title.
    body = client.get("/api/v1/tickets", params={"q": "streak"}).json()
    assert _ids(body["items"]) == ["CAD-118"]


# --------------------------------------------------------------------------- #
# Detail (real adapter) + error envelopes
# --------------------------------------------------------------------------- #


def test_real_detail_returns_full_ticket_with_rendered_body_and_run_state() -> None:
    client = TestClient(_real_app())
    resp = client.get("/api/v1/tickets/CAD-131")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "CAD-131"
    assert body["runState"] == "todo"
    assert "<h1>" in body["bodyHtml"]
    assert body["bodyMarkdown"].strip()


def test_detail_unknown_id_maps_to_ticket_not_found_404() -> None:
    client = TestClient(_real_app())
    resp = client.get("/api/v1/tickets/CAD-999")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ticket_not_found"


def test_detail_invalid_id_rejected_at_path_boundary_as_400() -> None:
    # A '$' is outside TICKET_ID_PATTERN, so the Path validator rejects it before
    # the handler runs — the adapter is never reached (that would be a 404).
    client = TestClient(_fake_app())
    resp = client.get("/api/v1/tickets/bad$id")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_ticket_id"


# --------------------------------------------------------------------------- #
# Frozen OpenAPI shape (what the frontend codegen freezes against)
# --------------------------------------------------------------------------- #


def test_openapi_publishes_ticket_paths_and_schemas() -> None:
    client = TestClient(_fake_app())
    schema = client.get("/api/v1/openapi.json").json()
    assert "/api/v1/tickets" in schema["paths"]
    assert "/api/v1/tickets/{ticket_id}" in schema["paths"]
    assert "Ticket" in schema["components"]["schemas"]
    assert "TicketSummary" in schema["components"]["schemas"]
    ref = schema["paths"]["/api/v1/tickets/{ticket_id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    assert ref.endswith("/Ticket")
