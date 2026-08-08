"""Shape tests for the fixture projects under ``tests/fixtures/projects/``.

These assert the invariants each fixture project encodes so the downstream
file-adapter, backend, and e2e tracks can rely on them. Standard library only
(``json`` + ``pathlib``) so the module runs identically under ``pytest`` from the
repo root or when executed directly.
"""

import json
import pathlib
import re

# ``parents[1]`` is the ``tests/`` directory regardless of the invoking cwd.
PROJECTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "projects"

RUN_STATES = ("todo", "in-flight", "ready", "merged")


def _load_manifest(project_name: str) -> dict:
    """Parse ``<project>/docs/planning/tickets.json`` and return the object."""
    manifest_path = PROJECTS_DIR / project_name / "docs" / "planning" / "tickets.json"
    return json.loads(manifest_path.read_text())


def test_minimal_manifest_has_exactly_three_tickets() -> None:
    manifest = _load_manifest("minimal")
    tickets = manifest["tickets"]
    assert len(tickets) == 3, (
        f"minimal fixture must declare exactly 3 tickets, found {len(tickets)}"
    )


def test_with_run_state_manifest_has_exactly_six_tickets() -> None:
    manifest = _load_manifest("with_run_state")
    tickets = manifest["tickets"]
    assert len(tickets) == 6, (
        f"with_run_state fixture must declare exactly 6 tickets, found {len(tickets)}"
    )


def test_with_run_state_has_a_marker_under_every_run_state() -> None:
    run_state_dir = PROJECTS_DIR / "with_run_state" / ".factory" / "run-state"
    assert run_state_dir.is_dir(), (
        f"with_run_state fixture must ship a run-state dir at {run_state_dir}"
    )
    for state in RUN_STATES:
        state_dir = run_state_dir / state
        assert state_dir.is_dir(), f"run-state dir must contain a '{state}' subdirectory"
        markers = list(state_dir.iterdir())
        assert markers, f"run-state '{state}' must contain at least one ticket marker"


def test_factory_v3_declares_a_json_path_for_every_ticket() -> None:
    # The shape `factory-ticket migrate` leaves: content is JSON and the INDEX says
    # where. The flat `<ticketsDir>/<id>.md` fallback is deliberately never exercised
    # here, because a migrated repository does not use it.
    tickets = _load_manifest("factory_v3")["tickets"]
    assert len(tickets) == 3, f"factory_v3 must declare exactly 3 tickets, found {len(tickets)}"
    for ticket in tickets:
        declared = ticket.get("path", "")
        assert declared.endswith(".json"), f"{ticket['id']} must point at a .json content file"
        assert (PROJECTS_DIR / "factory_v3" / declared).is_file(), (
            f"{ticket['id']} declares {declared}, which is not on disk"
        )


def test_factory_v3_run_state_carries_the_two_v3_additions() -> None:
    # `phase` and `subversion` are what v3 added to run-state, and a fixture without
    # them cannot exercise the views that read them. `phase: null` on a ticket that is
    # not mid-lane is written EXPLICITLY by the factory — absent would mean something
    # else, so the fixture must not economise on it.
    state = json.loads((PROJECTS_DIR / "factory_v3" / ".factory" / "run-state.json").read_text())
    assert state["subversion"]["name"] == "v1.0"
    assert state["tickets"]["T02"]["phase"] == "reviewing"
    for ticket_id in ("T01", "T03"):
        assert "phase" in state["tickets"][ticket_id]
        assert state["tickets"][ticket_id]["phase"] is None


def test_factory_v3_roadmap_carries_no_status_marker() -> None:
    # A committed checkbox fails the factory's own planning lint, so a fixture holding
    # one would be a project the factory refuses to run — and therefore not evidence.
    roadmap = (PROJECTS_DIR / "factory_v3" / "docs" / "planning" / "ROADMAP.md").read_text()
    assert not re.search(r"^\s*[-*+]\s+\[[ xX]\]", roadmap, re.MULTILINE), (
        "factory_v3's ROADMAP.md must carry no checkbox-shaped status marker"
    )


def test_malformed_manifest_fails_json_loads() -> None:
    manifest_path = PROJECTS_DIR / "malformed" / "docs" / "planning" / "tickets.json"
    raw = manifest_path.read_text()
    raised = False
    try:
        json.loads(raw)
    except json.JSONDecodeError:
        raised = True
    assert raised, (
        "malformed fixture's tickets.json must raise json.JSONDecodeError, "
        "but json.loads parsed it successfully"
    )
