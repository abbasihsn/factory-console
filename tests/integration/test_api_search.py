"""Integration tests for ``GET /api/v1/search`` (cross-ticket full-text search).

Drive an app built with FastAPI's ``TestClient`` over a seeded
:class:`FakeFileAdapter` whose tickets carry distinctive ``bodyMarkdown``, so the
assertions pin the endpoint's contract end to end: the ``{items, total}``
envelope on a matching query; a blank/whitespace ``q`` returning an empty result
(200, not 422 — ``q`` is present, just empty); an out-of-range ``limit`` rejected
at the ``Query`` boundary as the ``validation_error`` 422 envelope; a term that
appears ONLY in a ticket's BODY still hitting (proving body coverage, the
distinction from T22's id+title substring filter); and the frozen OpenAPI shape
publishing the route and both the ``SearchHit`` and ``SearchResponse`` schemas.
"""

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.app import create_app
from factory_console.domain import Project, Ticket
from factory_console.file_adapter import FakeFileAdapter

_FAKE_PROJECT = Project(
    rootPath=Path("/factory/demo-project"),
    ticketsManifestPath=Path("/factory/demo-project/docs/planning/tickets.json"),
    ticketsDir=Path("/factory/demo-project/docs/planning/tickets"),
    discoveredAt=datetime(2026, 7, 21, 12, 30, 0),
)


def _fake_ticket(ticket_id: str, *, title: str, body: str) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=title,
        status="todo",
        track="backend",
        milestone="MVP",
        filePath=Path(f"/factory/demo-project/docs/planning/tickets/{ticket_id}.md"),
        bodyMarkdown=body,
        bodyHtml=f"<p>{body}</p>",
        raw={"id": ticket_id},
    )


def _fake_app() -> FastAPI:
    """Build the real app over a FakeFileAdapter seeded with searchable bodies.

    Three tickets share the word "streak" in their bodies (so ``limit`` can
    truncate); FAKE-1's body additionally carries the unique word
    "photosynthesis", which appears in NO id or title anywhere — the body-only
    coverage probe.
    """
    tickets = [
        _fake_ticket(
            "FAKE-1",
            title="Alpha widget",
            body="The streak engine relies on photosynthesis as a codename.",
        ),
        _fake_ticket("FAKE-2", title="Beta gadget", body="A streak resets on a missed day."),
        _fake_ticket("FAKE-3", title="Gamma gizmo", body="Longest streak on the profile page."),
        _fake_ticket("FAKE-4", title="Delta doohickey", body="Weekly digest email delivery."),
    ]
    adapter = FakeFileAdapter(project=_FAKE_PROJECT, tickets=tickets)
    return create_app(adapter, version="0.0.0", project_root=Path("/factory/demo-project"))


def _ids(items: list[dict]) -> list[str]:
    return [item["ticket"]["id"] for item in items]


# --------------------------------------------------------------------------- #
# Happy path + envelope shape
# --------------------------------------------------------------------------- #


def test_search_returns_items_and_total_envelope() -> None:
    client = TestClient(_fake_app())
    resp = client.get("/api/v1/search", params={"q": "streak"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "total"}
    # Three tickets mention "streak" in their bodies; FAKE-4 does not.
    assert set(_ids(body["items"])) == {"FAKE-1", "FAKE-2", "FAKE-3"}
    assert body["total"] == len(body["items"]) == 3
    # Each hit carries the SearchHit shape.
    assert set(body["items"][0]) == {"ticket", "score", "matchedFields"}


def test_limit_truncates_the_result_and_total_matches() -> None:
    client = TestClient(_fake_app())
    body = client.get("/api/v1/search", params={"q": "streak", "limit": 1}).json()
    assert len(body["items"]) == 1
    assert body["total"] == 1


# --------------------------------------------------------------------------- #
# Body coverage — the distinction from T22's id+title-only filter
# --------------------------------------------------------------------------- #


def test_term_only_in_body_still_returns_the_ticket() -> None:
    # "photosynthesis" appears in FAKE-1's body only — no id or title contains it.
    client = TestClient(_fake_app())
    body = client.get("/api/v1/search", params={"q": "photosynthesis"}).json()
    assert _ids(body["items"]) == ["FAKE-1"]
    assert body["total"] == 1
    assert "bodyMarkdown" in body["items"][0]["matchedFields"]


# --------------------------------------------------------------------------- #
# Blank / whitespace q -> empty result (200, NOT 422)
# --------------------------------------------------------------------------- #


def test_blank_q_returns_empty_result_not_422() -> None:
    client = TestClient(_fake_app())
    resp = client.get("/api/v1/search", params={"q": ""})
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


def test_whitespace_only_q_returns_empty_result_not_422() -> None:
    client = TestClient(_fake_app())
    resp = client.get("/api/v1/search?q=%20%20")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


# --------------------------------------------------------------------------- #
# Validation envelopes
# --------------------------------------------------------------------------- #


def test_limit_below_range_is_validation_error_422() -> None:
    client = TestClient(_fake_app())
    resp = client.get("/api/v1/search", params={"q": "x", "limit": 0})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_limit_above_range_is_validation_error_422() -> None:
    client = TestClient(_fake_app())
    resp = client.get("/api/v1/search", params={"q": "x", "limit": 500})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_missing_q_is_validation_error_422() -> None:
    # q has no default, so omitting it entirely fails Query validation.
    client = TestClient(_fake_app())
    resp = client.get("/api/v1/search")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --------------------------------------------------------------------------- #
# Frozen OpenAPI shape (what the frontend codegen freezes against)
# --------------------------------------------------------------------------- #


def test_openapi_publishes_search_path_and_schemas() -> None:
    client = TestClient(_fake_app())
    schema = client.get("/api/v1/openapi.json").json()
    assert "/api/v1/search" in schema["paths"]
    assert "SearchHit" in schema["components"]["schemas"]
    assert "SearchResponse" in schema["components"]["schemas"]
    ref = schema["paths"]["/api/v1/search"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    assert ref.endswith("/SearchResponse")
