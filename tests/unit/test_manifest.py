"""Unit tests for the ``tickets.json`` manifest parser (``file_adapter/manifest.py``).

These pin the parser's forward-compatibility contract: unknown entry fields are
preserved on :attr:`Ticket.raw`, missing optionals default sensibly, the scalar
``provides`` string is coerced to ``list[str]``, and ``schemaVersion`` is surfaced
as a string but not enforced. Bad input (invalid JSON, a non-list ``tickets``)
raises :class:`MalformedManifest`. The happy/malformed paths use the real fixture
projects; ``tmp_path`` covers shapes the fixtures don't encode. Deterministic and
I/O-light — stdlib + pydantic only.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from factory_console.domain import Project, Ticket
from factory_console.file_adapter.manifest import (
    MalformedManifest,
    iter_ticket_stubs,
    load_manifest,
    manifest_entry_to_ticket_stub,
)

# ``parents[1]`` is the ``tests/`` directory regardless of the invoking cwd.
PROJECTS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "projects"

TICKETS_DIR = Path("/proj/docs/planning/tickets")


def _manifest_path(project_name: str) -> Path:
    return PROJECTS_DIR / project_name / "docs" / "planning" / "tickets.json"


def _fixture_project(project_name: str) -> Project:
    """A :class:`Project` pointing at a fixture project's real files."""
    root = PROJECTS_DIR / project_name
    return Project(
        rootPath=root,
        ticketsManifestPath=root / "docs" / "planning" / "tickets.json",
        ticketsDir=root / "docs" / "planning" / "tickets",
        discoveredAt=datetime(2026, 7, 20, 12, 0, 0),
    )


def _stubs_by_id(project_name: str) -> dict[str, Ticket]:
    return {stub.id: stub for stub in iter_ticket_stubs(_fixture_project(project_name))}


# --------------------------------------------------------------------------- #
# iter_ticket_stubs — one stub per entry, ids/titles as declared
# --------------------------------------------------------------------------- #


def test_iter_ticket_stubs_yields_one_stub_per_minimal_entry() -> None:
    stubs = list(iter_ticket_stubs(_fixture_project("minimal")))
    assert [stub.id for stub in stubs] == ["TM-001", "TM-015", "TM-028"]
    assert [stub.title for stub in stubs] == [
        "Ingest trail reports from the CSV drop folder",
        "Public trail-status REST endpoint",
        "Push alerts for washed-out trails",
    ]


# --------------------------------------------------------------------------- #
# Forward-compatibility — unknown fields preserved on raw
# --------------------------------------------------------------------------- #


def test_unknown_fields_are_preserved_on_raw() -> None:
    # TM-015 carries an `estimate` field the Ticket model does not name; it must
    # survive verbatim on `raw` for forward-compatibility with newer schemas.
    tm015 = _stubs_by_id("minimal")["TM-015"]
    assert tm015.raw["estimate"] == "M"

    _schema_version, entries = load_manifest(_manifest_path("minimal"))
    entry = next(e for e in entries if e["id"] == "TM-015")
    assert tm015.raw == entry


# --------------------------------------------------------------------------- #
# Missing optionals default sensibly
# --------------------------------------------------------------------------- #


def test_missing_optionals_default_sensibly() -> None:
    entry = {"id": "TM-900", "title": "Bare entry", "status": "todo"}
    stub = manifest_entry_to_ticket_stub(entry, TICKETS_DIR)
    assert stub.track is None
    assert stub.milestone is None
    assert stub.dependsOn == []
    assert stub.provides == []
    assert stub.files == []
    assert stub.bodyMarkdown == ""
    assert stub.bodyHtml == ""


# --------------------------------------------------------------------------- #
# provides — scalar string coerced to list[str]
# --------------------------------------------------------------------------- #


def test_provides_string_is_coerced_to_single_element_list() -> None:
    # The manifest stores `provides` as a scalar string; the stub exposes list[str].
    tm001 = _stubs_by_id("minimal")["TM-001"]
    assert tm001.provides == [
        "Nightly importer that folds ranger CSV exports into the canonical trail-report store"
    ]


def test_provides_list_passes_through_unchanged() -> None:
    entry = {"id": "TM-901", "provides": ["a", "b"]}
    stub = manifest_entry_to_ticket_stub(entry, TICKETS_DIR)
    assert stub.provides == ["a", "b"]


def test_provides_empty_string_becomes_empty_list() -> None:
    entry = {"id": "TM-902", "provides": ""}
    stub = manifest_entry_to_ticket_stub(entry, TICKETS_DIR)
    assert stub.provides == []


# --------------------------------------------------------------------------- #
# filePath is computed from tickets_dir + id
# --------------------------------------------------------------------------- #


def test_file_path_is_tickets_dir_joined_with_id() -> None:
    project = _fixture_project("minimal")
    stub = _stubs_by_id("minimal")["TM-001"]
    assert stub.filePath == project.ticketsDir / "TM-001.md"


# --------------------------------------------------------------------------- #
# id validation surfaces (delegated to the Ticket model regex)
# --------------------------------------------------------------------------- #


def test_invalid_ticket_id_surfaces_as_validation_error() -> None:
    entry = {"id": "bad/id", "title": "t", "status": "todo"}
    with pytest.raises(ValidationError):
        manifest_entry_to_ticket_stub(entry, TICKETS_DIR)


# --------------------------------------------------------------------------- #
# schemaVersion surfaced (coerced to str) but not enforced
# --------------------------------------------------------------------------- #


def test_load_manifest_surfaces_schema_version_as_string() -> None:
    schema_version, tickets = load_manifest(_manifest_path("minimal"))
    assert schema_version == "1"
    assert isinstance(tickets, list)
    assert len(tickets) == 3


def test_load_manifest_returns_none_schema_version_when_absent(tmp_path: Path) -> None:
    manifest_path = tmp_path / "tickets.json"
    manifest_path.write_text(json.dumps({"tickets": []}), encoding="utf-8")
    schema_version, tickets = load_manifest(manifest_path)
    assert schema_version is None
    assert tickets == []


# --------------------------------------------------------------------------- #
# Malformed input raises MalformedManifest
# --------------------------------------------------------------------------- #


def test_load_manifest_raises_on_malformed_json() -> None:
    # The malformed fixture has a trailing comma, so json.loads raises.
    with pytest.raises(MalformedManifest) as exc_info:
        load_manifest(_manifest_path("malformed"))
    exc = exc_info.value
    assert exc.code == "malformed_manifest"
    assert exc.status == 500


def test_malformed_manifest_chains_cause_and_carries_only_path() -> None:
    manifest_path = _manifest_path("malformed")
    with pytest.raises(MalformedManifest) as exc_info:
        load_manifest(manifest_path)
    exc = exc_info.value
    assert isinstance(exc.__cause__, json.JSONDecodeError)
    assert isinstance(exc.cause, json.JSONDecodeError)
    assert exc.details == {"path": str(manifest_path)}


def test_load_manifest_raises_when_tickets_is_not_a_list(tmp_path: Path) -> None:
    manifest_path = tmp_path / "tickets.json"
    manifest_path.write_text(json.dumps({"schemaVersion": 1, "tickets": "nope"}), encoding="utf-8")
    with pytest.raises(MalformedManifest):
        load_manifest(manifest_path)


def test_load_manifest_raises_when_tickets_key_is_missing(tmp_path: Path) -> None:
    manifest_path = tmp_path / "tickets.json"
    manifest_path.write_text(json.dumps({"schemaVersion": 1}), encoding="utf-8")
    with pytest.raises(MalformedManifest):
        load_manifest(manifest_path)


def test_load_manifest_raises_when_top_level_is_not_an_object(tmp_path: Path) -> None:
    manifest_path = tmp_path / "tickets.json"
    manifest_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(MalformedManifest):
        load_manifest(manifest_path)
