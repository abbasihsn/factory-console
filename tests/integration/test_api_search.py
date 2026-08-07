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
Since v3.0 the handler resolves its root through the selection seam, so the two ways
that resolution can refuse — nothing selected, and a selected path that is gone — are
pinned as 409s rather than as the empty result a blank ``q`` legitimately gives.
"""

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.app import create_app
from factory_console.domain import Project, Ticket
from factory_console.file_adapter import FakeFileAdapter
from factory_console.services.project_selection import SelectionState
from factory_console.store.fake_registry import FakeProjectRegistry

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


def _fake_app(
    *,
    project_root: Path = Path("/factory/demo-project"),
    registry: FakeProjectRegistry | None = None,
) -> FastAPI:
    """Build the real app over a FakeFileAdapter seeded with searchable bodies.

    Three tickets share the word "streak" in their bodies (so ``limit`` can
    truncate); FAKE-1's body additionally carries the unique word
    "photosynthesis", which appears in NO id or title anywhere — the body-only
    coverage probe.

    ``project_root`` and ``registry`` are the selection seam's two inputs: leaving
    both at their defaults is pinned mode (what every other case here asserts), and
    passing a registry lets a case drive the SELECTED project instead.
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
    return create_app(
        adapter,
        version="0.0.0",
        project_root=project_root,
        project_registry=registry,
    )


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
# The selection seam: no project resolved is a 409, never an empty result
# --------------------------------------------------------------------------- #


def test_search_refuses_with_409_when_nothing_is_selected() -> None:
    # ``{items: [], total: 0}`` is a statement ABOUT a project — "nothing here
    # matches" — so it must not be the answer when there is no project to search.
    app = _fake_app()
    app.state.selection = SelectionState(pinned_root=None, registry=None)

    resp = TestClient(app).get("/api/v1/search", params={"q": "streak"})

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_project_selected"


def test_search_refuses_with_409_when_the_selected_path_is_gone(tmp_path: Path) -> None:
    # Resolution refuses rather than falling back to the pinned root, which would
    # rank one project's tickets under another project's name.
    gone = tmp_path / "gone"
    gone.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(gone)
    app = _fake_app(project_root=tmp_path / "pinned", registry=registry)
    app.state.selection.select(row.id)
    gone.rmdir()

    resp = TestClient(app).get("/api/v1/search", params={"q": "streak"})

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "selected_project_unavailable"


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
