"""Shape contract for the fixture projects under ``tests/fixtures/projects/``.

Self-contained: uses only stdlib ``json``/``pathlib`` so it runs before the
file-adapter parser modules (T07/T12/T13) exist. Fixtures are read-only — this
suite only reads them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Locate fixtures relative to THIS file, never via cwd, so pytest passes from
# any working directory.
PROJECTS_DIR = Path(__file__).parent.parent / "fixtures" / "projects"

# The manifest keys the domain model / T12 parser bind to. Anything outside this
# set on a ticket entry lands on ``Ticket.raw`` (schema-tolerant passthrough).
KNOWN_TICKET_KEYS = {
    "id",
    "title",
    "status",
    "track",
    "milestone",
    "dependsOn",
    "provides",
    "files",
}

# Every RunState marker directory the run-state contract defines (minus the
# derived ``unknown``/``todo`` fallbacks).
RUN_STATE_DIRS = ("todo", "in-flight", "ready", "merged")


def _load_manifest(project: str) -> dict:
    manifest_path = PROJECTS_DIR / project / "docs" / "planning" / "tickets.json"
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def test_minimal_manifest_parses_with_three_tickets() -> None:
    manifest = _load_manifest("minimal")
    assert manifest["schemaVersion"] == 1
    assert len(manifest["tickets"]) == 3


def test_minimal_has_no_factory_dir() -> None:
    # No run-state dir → downstream run-state probes must resolve to ``unknown``.
    assert not (PROJECTS_DIR / "minimal" / ".factory").exists()


def test_minimal_has_a_ticket_with_an_unknown_extra_field() -> None:
    # Proves ``Ticket.raw`` passthrough coverage: at least one entry carries a
    # key outside the known manifest schema.
    manifest = _load_manifest("minimal")
    tickets_with_extra_keys = [
        ticket for ticket in manifest["tickets"] if set(ticket) - KNOWN_TICKET_KEYS
    ]
    assert tickets_with_extra_keys, "expected one minimal ticket with an unknown field"


def test_with_run_state_manifest_parses_with_six_tickets() -> None:
    manifest = _load_manifest("with_run_state")
    assert len(manifest["tickets"]) == 6


def test_with_run_state_has_a_marker_for_every_enum_state() -> None:
    run_state_dir = PROJECTS_DIR / "with_run_state" / ".factory" / "run-state"
    for state in RUN_STATE_DIRS:
        state_dir = run_state_dir / state
        assert state_dir.is_dir(), f"missing run-state dir: {state}"
        markers = [child for child in state_dir.iterdir() if child.name != ".gitkeep"]
        assert markers, f"run-state dir {state!r} has no ticket marker"


def test_with_run_state_has_an_unresolved_dependency() -> None:
    # Proves ``unresolvedDeps`` coverage: some ticket depends on an id that is
    # not present anywhere in the manifest.
    manifest = _load_manifest("with_run_state")
    manifest_ids = {ticket["id"] for ticket in manifest["tickets"]}
    all_deps = {
        dep for ticket in manifest["tickets"] for dep in ticket.get("dependsOn", [])
    }
    unresolved = all_deps - manifest_ids
    assert unresolved, "expected a dependsOn id absent from the manifest"


def test_malformed_manifest_fails_to_parse() -> None:
    manifest_path = (
        PROJECTS_DIR / "malformed" / "docs" / "planning" / "tickets.json"
    )
    with pytest.raises(json.JSONDecodeError):
        json.loads(manifest_path.read_text(encoding="utf-8"))
