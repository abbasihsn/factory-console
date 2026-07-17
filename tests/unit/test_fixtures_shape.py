"""Shape checks for the read-only fixture projects under ``tests/fixtures/projects``.

These tests pin the invariants each fixture encodes so downstream file-adapter,
backend, and e2e tracks can rely on them. They are deterministic and strictly
read-only: nothing here writes into the fixtures. Stdlib only (``json`` +
``pathlib``) so the suite runs before any backend dependencies exist.
"""

import json
from pathlib import Path

import pytest

# Resolve fixtures relative to this file, never via the current working directory,
# so the tests pass regardless of where pytest is invoked from.
PROJECTS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "projects"

RUN_STATES = ("todo", "in-flight", "ready", "merged")


def _manifest(project: str) -> dict:
    """Parse ``<project>/docs/planning/tickets.json`` and return the object."""
    manifest_path = PROJECTS_DIR / project / "docs" / "planning" / "tickets.json"
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def test_minimal_has_three_tickets() -> None:
    manifest = _manifest("minimal")
    assert len(manifest["tickets"]) == 3


def test_minimal_has_no_factory_dir() -> None:
    # Absence of .factory/ is what makes run-state probes return `unknown`.
    assert not (PROJECTS_DIR / "minimal" / ".factory").exists()


def test_minimal_carries_an_unknown_extra_field() -> None:
    # Exercises `Ticket.raw` passthrough of fields the model does not name.
    known = {
        "id", "title", "status", "track", "milestone",
        "dependsOn", "provides", "files",
    }
    extras = {
        key
        for ticket in _manifest("minimal")["tickets"]
        for key in ticket
        if key not in known
    }
    assert extras, "expected at least one unknown extra field (e.g. 'estimate')"


def test_with_run_state_has_six_tickets() -> None:
    manifest = _manifest("with_run_state")
    assert len(manifest["tickets"]) == 6


def test_with_run_state_has_markers_for_every_state() -> None:
    run_state_dir = PROJECTS_DIR / "with_run_state" / ".factory" / "run-state"
    for state in RUN_STATES:
        markers = list((run_state_dir / state).iterdir())
        assert markers, f"expected at least one marker under run-state/{state}"


def test_with_run_state_mixes_file_and_directory_markers() -> None:
    run_state_dir = PROJECTS_DIR / "with_run_state" / ".factory" / "run-state"
    # Per ARCHITECTURE: todo/merged are marker files, in-flight/ready are dirs.
    assert (run_state_dir / "todo" / "S01").is_file()
    assert (run_state_dir / "merged" / "S05").is_file()
    assert (run_state_dir / "in-flight" / "S03").is_dir()
    assert (run_state_dir / "ready" / "S04").is_dir()


def test_with_run_state_s06_has_no_marker() -> None:
    # S06 is in the manifest but carries no run-state marker; the contract treats
    # an unmarked ticket as `todo`. Pin that it stays unmarked in every state dir.
    run_state_dir = PROJECTS_DIR / "with_run_state" / ".factory" / "run-state"
    marked = {
        marker.name
        for state in RUN_STATES
        for marker in (run_state_dir / state).iterdir()
    }
    assert "S06" not in marked


def test_with_run_state_has_an_unresolved_dependency() -> None:
    manifest = _manifest("with_run_state")
    ids = {ticket["id"] for ticket in manifest["tickets"]}
    dangling = {
        dep
        for ticket in manifest["tickets"]
        for dep in ticket["dependsOn"]
        if dep not in ids
    }
    assert dangling, "expected a dependsOn edge pointing outside the manifest"


def test_with_run_state_body_embeds_a_script_payload() -> None:
    # The <script> snippet drives end-to-end HTML sanitization downstream.
    body = (
        PROJECTS_DIR
        / "with_run_state"
        / "docs"
        / "planning"
        / "tickets"
        / "S04.md"
    ).read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" in body


def test_malformed_manifest_fails_to_parse() -> None:
    manifest_path = (
        PROJECTS_DIR / "malformed" / "docs" / "planning" / "tickets.json"
    )
    with pytest.raises(json.JSONDecodeError):
        json.loads(manifest_path.read_text(encoding="utf-8"))
