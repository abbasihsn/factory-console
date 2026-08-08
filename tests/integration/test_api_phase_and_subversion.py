"""Integration tests for the two v3 run-state additions, end to end over HTTP.

Driven over the checked-in ``factory_v3`` fixture through a :class:`RealFileAdapter`, so
these read the same ``.factory/run-state.json`` a real project has: T01 merged with no
phase, T02 ``in_progress`` mid-``reviewing``, T03 todo, and an open ``v1.0`` sub-version
whose PR has not been cut yet.

The unit tests next door pin what the READER does with each shape; these pin that the
values survive the projection, the service, the response models and the JSON encoder —
the four places a field silently goes missing.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.app import create_app
from factory_console.file_adapter.real import RealFileAdapter

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "projects" / "factory_v3"


def _app(root: Path = FIXTURE) -> FastAPI:
    return create_app(RealFileAdapter(), version="0.0.0", project_root=root)


def _copy(tmp_path: Path) -> Path:
    """A private copy of the fixture, for the cases that edit run-state.

    The checked-in fixture is shared by the read-side suites and must never be mutated.
    """
    import shutil

    destination = tmp_path / "project"
    shutil.copytree(FIXTURE, destination)
    return destination


# --------------------------------------------------------------------------- #
# phase reaches both ticket views
# --------------------------------------------------------------------------- #


def test_the_list_view_carries_each_tickets_phase() -> None:
    rows = TestClient(_app()).get("/api/v1/tickets").json()["items"]
    by_id = {row["id"]: row for row in rows}

    assert by_id["T02"]["runState"] == "in_progress"
    assert by_id["T02"]["phase"] == "reviewing"
    # A merged ticket is not mid-lane: the factory cleared its phase on the transition.
    assert by_id["T01"]["phase"] is None
    assert by_id["T03"]["phase"] is None


def test_the_detail_view_carries_the_phase_too() -> None:
    # The two paths resolve run-state differently on purpose — the list through the
    # projection, the detail through its own probe — so a field can reach one and not
    # the other. That has happened before; this is the guard.
    ticket = TestClient(_app()).get("/api/v1/tickets/T02").json()

    assert ticket["runState"] == "in_progress"
    assert ticket["phase"] == "reviewing"


def test_the_list_and_detail_views_agree_about_the_phase() -> None:
    client = TestClient(_app())

    rows = {row["id"]: row for row in client.get("/api/v1/tickets").json()["items"]}
    for ticket_id in ("T01", "T02", "T03"):
        detail = client.get(f"/api/v1/tickets/{ticket_id}").json()
        assert detail["phase"] == rows[ticket_id]["phase"], ticket_id


def test_an_unrecognised_phase_does_not_make_the_ticket_unwritable(tmp_path: Path) -> None:
    # THE case this pair of fields most needed a guard for. An unrecognised STATUS
    # resolves `unreadable` and both write gates refuse it; a phase must do nothing of
    # the kind, or a cosmetic field becomes a project-wide write lockout.
    root = _copy(tmp_path)
    state_path = root / ".factory" / "run-state.json"
    document = json.loads(state_path.read_text(encoding="utf-8"))
    document["tickets"]["T03"]["phase"] = "auditing"
    state_path.write_text(json.dumps(document), encoding="utf-8")

    ticket = TestClient(_app(root)).get("/api/v1/tickets/T03").json()

    assert ticket["phase"] == "auditing", "carried through verbatim, not dropped"
    assert ticket["runState"] == "todo", "and the status is untouched"


# --------------------------------------------------------------------------- #
# subversion reaches the project payload
# --------------------------------------------------------------------------- #


def test_the_project_payload_names_the_open_subversion() -> None:
    body = TestClient(_app()).get("/api/v1/project").json()

    assert body["subversion"] == {
        "branch": "factory/v1.0",
        "baseSha": "0123456789abcdef0123456789abcdef01234567",
        "name": "v1.0",
        # Cut, but no PR opened yet — a sub-version being BUILT rather than one
        # WAITING on a human.
        "prUrl": None,
    }


def test_a_project_between_cuts_reports_no_subversion(tmp_path: Path) -> None:
    # The normal state: the factory deletes the record when the branch lands on main.
    root = _copy(tmp_path)
    state_path = root / ".factory" / "run-state.json"
    document = json.loads(state_path.read_text(encoding="utf-8"))
    del document["subversion"]
    state_path.write_text(json.dumps(document), encoding="utf-8")

    body = TestClient(_app(root)).get("/api/v1/project").json()

    assert body["subversion"] is None


def test_the_subversion_read_does_not_disturb_the_rest_of_the_payload() -> None:
    body = TestClient(_app()).get("/api/v1/project").json()

    assert body["rootPath"] == str(FIXTURE)
    assert body["ticketsManifestPath"].endswith("docs/planning/tickets.json")
    # A JSON-sourced project has no marker directory — unchanged by this PR, and worth
    # re-pinning here because `ProjectView` is a new model and could have dropped it.
    assert body["runStateDir"] is None
