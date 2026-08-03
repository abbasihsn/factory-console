"""Unit tests for :class:`RunService` over a :class:`FakeFileAdapter` + real files.

The manifest is faked (the seeded adapter answers ``list_tickets``) and the
artifacts are REAL: each case writes ``.factory/results/<id>.json`` and
``.factory/receipts/<id>.json`` under ``tmp_path`` and lets T88's readers do the
reading, because what is under test is precisely the composition of on-disk
evidence onto the manifest — stubbing the reads would test the stub.

Every absence assertion is made PER SOURCE, never as a count: "one artifact is
missing" is not an answer an operator can act on, and a record that said only
that would satisfy a count assertion while losing the fact this milestone exists
to show.
"""

import json
from datetime import datetime
from pathlib import Path

from factory_console.domain import Project, Ticket
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.runs import RECEIPTS_RELATIVE_DIR, RESULTS_RELATIVE_DIR
from factory_console.services.run_service import RunService

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "runs"


def _make_project(root: Path) -> Project:
    return Project(
        rootPath=root,
        ticketsManifestPath=root / "docs/planning/tickets.json",
        ticketsDir=root / "docs/planning/tickets",
        roadmapPath=root / "ROADMAP.md",
        runStateDir=root / ".factory/run-state",
        discoveredAt=datetime(2026, 8, 3, 12, 0, 0),
    )


def _make_ticket(ticket_id: str) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=f"Ticket {ticket_id}",
        status="todo",
        track="backend",
        milestone="v2.1",
        filePath=Path(f"/proj/docs/planning/tickets/{ticket_id}.md"),
        bodyMarkdown=f"# {ticket_id}",
        bodyHtml=f"<h1>{ticket_id}</h1>",
        raw={"id": ticket_id},
    )


def _service(root: Path, ticket_ids: list[str]) -> tuple[RunService, Project]:
    project = _make_project(root)
    fake = FakeFileAdapter(project=project, tickets=[_make_ticket(i) for i in ticket_ids])
    return RunService(fake), project


def _write_artifact(project_root: Path, relative: Path, text: str) -> Path:
    """Write ``text`` at ``project_root / relative`` and return the created path."""
    path = project_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_both(project_root: Path, ticket_id: str) -> tuple[dict, dict]:
    """Write the illustrative result + receipt fixtures for ``ticket_id``."""
    result_text = (FIXTURES / "result.json").read_text(encoding="utf-8")
    receipt_text = (FIXTURES / "receipt.json").read_text(encoding="utf-8")
    _write_artifact(project_root, RESULTS_RELATIVE_DIR / f"{ticket_id}.json", result_text)
    _write_artifact(project_root, RECEIPTS_RELATIVE_DIR / f"{ticket_id}.json", receipt_text)
    return json.loads(result_text), json.loads(receipt_text)


# --------------------------------------------------------------------------- #
# Every artifact present -> a fully populated record
# --------------------------------------------------------------------------- #


def test_a_ticket_with_every_artifact_yields_a_fully_populated_record(tmp_path: Path) -> None:
    result_data, receipt_data = _write_both(tmp_path, "T89")
    service, project = _service(tmp_path, ["T89"])

    (record,) = service.list_run_records(project)

    assert record.ticketId == "T89"
    assert record.result.reason is None
    assert record.receipt.reason is None
    assert record.result.data == result_data
    assert record.receipt.data == receipt_data


# --------------------------------------------------------------------------- #
# No artifacts -> EACH absent source named, asserted per source
# --------------------------------------------------------------------------- #


def test_a_ticket_with_no_artifacts_names_each_absent_source(tmp_path: Path) -> None:
    service, project = _service(tmp_path, ["T89"])

    (record,) = service.list_run_records(project)

    # Per source, deliberately: a count of missing artifacts would pass here and
    # tell an operator nothing about WHICH one is missing.
    assert record.result.reason == "absent"
    assert record.result.data is None
    assert record.receipt.reason == "absent"
    assert record.receipt.data is None


def test_one_absent_source_beside_one_present_source_is_reported_independently(
    tmp_path: Path,
) -> None:
    # A lane that wrote a result and no receipt: the two sources are independent
    # and neither reason may be inferred from the other.
    _write_artifact(tmp_path, RESULTS_RELATIVE_DIR / "T89.json", '{"status":"ready"}')
    service, project = _service(tmp_path, ["T89"])

    (record,) = service.list_run_records(project)

    assert record.result.reason is None
    assert record.result.data == {"status": "ready"}
    assert record.receipt.reason == "absent"


# --------------------------------------------------------------------------- #
# Malformed -> distinguished from absent
# --------------------------------------------------------------------------- #


def test_a_malformed_artifact_is_unparseable_not_absent(tmp_path: Path) -> None:
    _write_artifact(tmp_path, RESULTS_RELATIVE_DIR / "T89.json", "{not json at all")
    service, project = _service(tmp_path, ["T89"])

    (record,) = service.list_run_records(project)

    assert record.result.reason == "unparseable", "a file that is there is not 'absent'"
    assert record.receipt.reason == "absent", "and the missing source keeps its own reason"


def test_valid_json_of_the_wrong_shape_is_unparseable(tmp_path: Path) -> None:
    # Valid JSON whose top-level document is not an object: it answered,
    # unintelligibly, which is not the same as never having been written.
    _write_artifact(tmp_path, RECEIPTS_RELATIVE_DIR / "T89.json", "[1, 2, 3]")
    service, project = _service(tmp_path, ["T89"])

    (record,) = service.list_run_records(project)

    assert record.receipt.reason == "unparseable"
    assert record.receipt.data is None


# --------------------------------------------------------------------------- #
# The manifest is the list — a never-run ticket is a record, not an omission
# --------------------------------------------------------------------------- #


def test_a_manifest_ticket_the_factory_never_ran_is_still_present(tmp_path: Path) -> None:
    _write_both(tmp_path, "T88")
    service, project = _service(tmp_path, ["T88", "T89"])

    records = service.list_run_records(project)

    assert [record.ticketId for record in records] == ["T88", "T89"], (
        "one record per MANIFEST ticket, in manifest order — the never-run ticket "
        "is not filtered out"
    )
    never_ran = records[1]
    assert never_ran.result.reason == "absent"
    assert never_ran.receipt.reason == "absent"


def test_an_artifact_with_no_manifest_ticket_yields_no_record(tmp_path: Path) -> None:
    # The converse: the artifact directory is evidence, not the list. A stray
    # result for an id the manifest does not name must not invent a record.
    _write_both(tmp_path, "T404")
    service, project = _service(tmp_path, ["T89"])

    records = service.list_run_records(project)

    assert [record.ticketId for record in records] == ["T89"]


def test_an_empty_manifest_yields_no_records(tmp_path: Path) -> None:
    service, project = _service(tmp_path, [])
    assert service.list_run_records(project) == []
