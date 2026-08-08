"""Integration tests for the widened ``GET /api/v1/roadmap`` endpoint.

Drive apps built with FastAPI's ``TestClient`` over both adapters. A seeded
:class:`FakeFileAdapter` carrying a full :class:`Roadmap` pins the present branch
(the rendered body plus structured ``milestones[]``, and NO ``present`` key), and
a fake seeded WITHOUT a roadmap pins the ``{present: false}`` branch cleanly. A
:class:`RealFileAdapter` over a ``tmp_path`` project whose ``ROADMAP.md`` is
non-UTF-8 pins the ``roadmap_unreadable`` 500 envelope, and the OpenAPI test pins
the frozen widened ``Roadmap`` schema the frontend codegen freezes against.
"""

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.app import create_app
from factory_console.domain import Project, Roadmap, RunState
from factory_console.domain.deps import RoadmapItem, RoadmapMilestone
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.real import RealFileAdapter
from factory_console.services.project_selection import SelectionState
from factory_console.store.fake_registry import FakeProjectRegistry

_FAKE_PROJECT = Project(
    rootPath=Path("/factory/demo-project"),
    ticketsManifestPath=Path("/factory/demo-project/docs/planning/tickets.json"),
    ticketsDir=Path("/factory/demo-project/docs/planning/tickets"),
    roadmapPath=Path("/factory/demo-project/ROADMAP.md"),
    discoveredAt=datetime(2026, 7, 21, 12, 30, 0),
)

_FAKE_ROADMAP = Roadmap(
    path=Path("/factory/demo-project/ROADMAP.md"),
    bodyMarkdown="# Roadmap\n\n## MVP\n\n- T01 Ship it\n- Write the announcement post\n",
    bodyHtml="<h1>Roadmap</h1>\n<h2>MVP</h2>\n<ul>\n<li>T01 Ship it</li>\n</ul>",
    milestones=[
        RoadmapMilestone(
            name="MVP",
            items=[
                RoadmapItem(text="Ship it", ticketId="T01"),
                # A prose bullet naming no ticket — the item whose status stays null.
                RoadmapItem(text="Write the announcement post"),
            ],
        ),
    ],
)
"""A roadmap as the PARSER produces it: no ``runState`` on any item yet.

Deliberately unresolved. The endpoint's job is to fill it in from the run-state source,
so seeding it here would test the fixture rather than the join.
"""


def _present_app(run_states: dict[str, RunState] | None = None) -> FastAPI:
    """Build the app over a FakeFileAdapter whose project has a full roadmap."""
    adapter = FakeFileAdapter(
        project=_FAKE_PROJECT, tickets=[], roadmap=_FAKE_ROADMAP, run_states=run_states
    )
    return create_app(adapter, version="0.0.0", project_root=_FAKE_PROJECT.rootPath)


def _absent_app(
    *,
    project_root: Path = _FAKE_PROJECT.rootPath,
    registry: FakeProjectRegistry | None = None,
) -> FastAPI:
    """Build the app over a FakeFileAdapter whose project has no roadmap.

    ``get_roadmap`` returns the seeded ``roadmap`` verbatim, which defaults to
    ``None`` here, so this pins the ``{present: false}`` branch.
    """
    adapter = FakeFileAdapter(project=_FAKE_PROJECT, tickets=[])
    return create_app(
        adapter,
        version="0.0.0",
        project_root=project_root,
        project_registry=registry,
    )


def test_roadmap_present_returns_full_body_and_milestones() -> None:
    client = TestClient(_present_app())
    resp = client.get("/api/v1/roadmap")
    assert resp.status_code == 200
    body = resp.json()
    # Full rendered body is served, not a presence probe.
    assert body["bodyHtml"]
    assert body["bodyMarkdown"]
    # Structured milestones are present and non-empty.
    assert body["milestones"]
    assert body["milestones"][0]["items"]
    # The present branch carries NO ``present`` key — the frontend discriminates
    # the Roadmap from RoadmapAbsent on the absence of that field.
    assert "present" not in body


def test_roadmap_items_carry_the_run_state_the_source_reports() -> None:
    # The whole point of the endpoint since v3 §4: status comes from the run-state
    # source, per request, not from a checkbox somebody remembered to tick.
    client = TestClient(_present_app(run_states={"T01": RunState.merged}))

    items = client.get("/api/v1/roadmap").json()["milestones"][0]["items"]

    assert items[0]["runState"] == "merged"


def test_a_roadmap_item_naming_no_ticket_has_a_null_run_state() -> None:
    # `null` is not `unknown`. `unknown` means a source was asked and said nothing;
    # `null` means there was no question, because a prose bullet has no ticket. Badging
    # these would fill the view with claims about tickets that do not exist.
    client = TestClient(_present_app(run_states={"T01": RunState.merged}))

    items = client.get("/api/v1/roadmap").json()["milestones"][0]["items"]

    assert items[1]["ticketId"] is None
    assert items[1]["runState"] is None


def test_an_unseeded_ticket_resolves_rather_than_going_missing() -> None:
    # No seeded state at all: the item still carries the source's answer for it, so the
    # view never shows a ticket-bearing item with nothing where a status belongs.
    client = TestClient(_present_app())

    items = client.get("/api/v1/roadmap").json()["milestones"][0]["items"]

    assert items[0]["runState"] == "unknown"


def test_the_document_no_longer_decides_status() -> None:
    # The checkbox is gone from the wire entirely — not merely ignored. Leaving `done`
    # in the payload beside a live `runState` would publish two answers to one question,
    # and a client picking the wrong one would be reading a stale hand-ticked claim.
    client = TestClient(_present_app())

    items = client.get("/api/v1/roadmap").json()["milestones"][0]["items"]

    assert "done" not in items[0]


def test_roadmap_absent_returns_present_false() -> None:
    client = TestClient(_absent_app())
    resp = client.get("/api/v1/roadmap")
    assert resp.status_code == 200
    assert resp.json() == {"present": False}


def test_roadmap_unreadable_returns_500_envelope(tmp_path: Path) -> None:
    # A discovered ROADMAP.md that cannot be decoded as UTF-8 surfaces as the
    # mapped ``roadmap_unreadable`` 500 envelope via the domain-error handler —
    # the handler catches nothing and lets RoadmapUnreadable propagate.
    planning = tmp_path / "docs" / "planning"
    planning.mkdir(parents=True)
    (planning / "tickets.json").write_text('{"schemaVersion": 1, "tickets": []}', encoding="utf-8")
    (tmp_path / "ROADMAP.md").write_bytes(b"\xff\xfe not valid utf-8")
    app = create_app(RealFileAdapter(), version="0.0.0", project_root=tmp_path)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/roadmap")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "roadmap_unreadable"


def test_roadmap_refuses_with_409_when_nothing_is_selected() -> None:
    # ``{present: false}`` is a statement ABOUT a project — "this one ships no
    # roadmap" — so it must not be the answer when there is no project to make it
    # about. The absence of a selection is a different, named condition.
    app = _absent_app()
    app.state.selection = SelectionState(pinned_root=None, registry=None)

    resp = TestClient(app).get("/api/v1/roadmap")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_project_selected"


def test_roadmap_refuses_with_409_when_the_selected_path_is_gone(tmp_path: Path) -> None:
    gone = tmp_path / "gone"
    gone.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(gone)
    app = _absent_app(project_root=tmp_path / "pinned", registry=registry)
    app.state.selection.select(row.id)
    gone.rmdir()

    resp = TestClient(app).get("/api/v1/roadmap")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "selected_project_unavailable"


def test_openapi_publishes_widened_roadmap_schema() -> None:
    client = TestClient(_absent_app())
    resp = client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "/api/v1/roadmap" in schema["paths"]
    # The widened Roadmap component (with its structured milestones) is reachable
    # in the schema the frontend codegen freezes against.
    components = schema["components"]["schemas"]
    assert "Roadmap" in components
    assert "milestones" in components["Roadmap"]["properties"]
