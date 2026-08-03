"""Unit tests for :class:`RunService` over its two ports.

The manifest is faked (the seeded :class:`FakeFileAdapter` answers
``list_tickets``) and the artifacts are read through the REAL
:class:`RealRunArtifactReader` against files each case writes under ``tmp_path``:
what is under test is precisely the composition of on-disk evidence onto the
manifest, so stubbing the reads would test the stub.

That the reads go through the
:class:`~factory_console.file_adapter.run_artifacts.RunArtifactReader` port rather
than around it is itself covered — see
``test_the_service_is_substitutable_over_both_ports``, which composes a POPULATED
record with no filesystem at all. That case is unreachable without the port, and
its absence is what let a fake-backed caller answer ``absent`` for every source
while appearing to be under test.

Every absence assertion is made PER SOURCE, never as a count: "one artifact is
missing" is not an answer an operator can act on, and a record that said only
that would satisfy a count assertion while losing the fact this milestone exists
to show.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest
from _read_only_guard import (
    assert_module_carries_read_only_header,
    assert_module_is_read_only,
)

from factory_console.domain import Project, Ticket
from factory_console.domain.runs import ArtifactRead
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter import run_artifacts as run_artifacts_module
from factory_console.file_adapter.run_artifacts import (
    FakeRunArtifactReader,
    RealRunArtifactReader,
    RunArtifactReader,
)
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
    """A service reading the REAL artifacts under ``root`` for a faked manifest."""
    return _service_with(root, ticket_ids, RealRunArtifactReader())


def _service_with(
    root: Path, ticket_ids: list[str], artifacts: RunArtifactReader
) -> tuple[RunService, Project]:
    project = _make_project(root)
    fake = FakeFileAdapter(project=project, tickets=[_make_ticket(i) for i in ticket_ids])
    return RunService(fake, artifacts), project


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


# --------------------------------------------------------------------------- #
# A path-unsafe manifest id degrades that ONE record, never the whole listing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("unsafe_id", [".", ".."])
def test_a_path_unsafe_manifest_id_degrades_only_its_own_record(
    tmp_path: Path, unsafe_id: str
) -> None:
    """A bare ``.``/``..`` id is reported as unreadable, not raised past its neighbours.

    ``TICKET_ID_PATTERN`` is ``^[A-Za-z0-9_.-]+$``, so a dot-only id satisfies the
    model boundary and reaches the readers, which reject it as a single-segment
    traversal. Degrading is what keeps one malformed manifest entry from deleting
    every healthy ticket's record — the same trade ``RealFileAdapter._safe_run_state``
    already makes for this id class on the ``list_tickets`` path.
    """
    _write_both(tmp_path, "T88")
    service, project = _service(tmp_path, ["T88", unsafe_id])

    records = service.list_run_records(project)

    assert [record.ticketId for record in records] == ["T88", unsafe_id], (
        "the malformed id gets a record like any other manifest ticket, and its "
        "healthy neighbour survives"
    )
    healthy = records[0]
    assert healthy.result.reason is None, "the neighbour's artifacts are still read"
    assert healthy.receipt.reason is None

    refused = records[1]
    # ``unreadable``, per source: the console refused to look. NOT ``absent``, which
    # would claim the factory wrote nothing — a fact nothing here established.
    assert refused.result.reason == "unreadable"
    assert refused.receipt.reason == "unreadable"
    assert refused.result.data is None
    assert refused.receipt.data is None
    # Each refusal names its OWN artifact. Asserted because the two sources are
    # refused by one shared helper parameterized by directory, so a crossed pair
    # would report the receipt's path under the result and be invisible to any
    # assertion that only checks the reason.
    #
    # Compared against the RESOLVED directory, per the convention every other
    # ``ArtifactRead.path`` assertion follows (``tests/unit/test_runs.py``): a
    # refusal reports the path through ``runs.refusal_path``, so on a platform whose
    # temp dir is a symlink (macOS) the unresolved spelling would not match.
    assert refused.result.path.parent == (tmp_path / RESULTS_RELATIVE_DIR).resolve()
    assert refused.receipt.path.parent == (tmp_path / RECEIPTS_RELATIVE_DIR).resolve()


# --------------------------------------------------------------------------- #
# The service reads through its ports, not around them
# --------------------------------------------------------------------------- #


def test_the_service_is_substitutable_over_both_ports() -> None:
    """Handed two fakes, the service composes a POPULATED record and touches no disk.

    The point of the :class:`RunArtifactReader` port. ``rootPath`` here is
    ``/proj`` — the house convention for fake-backed service tests, and a path that
    does not exist — so a service that called T88's readers directly would answer
    ``absent`` for every source no matter what it was seeded with, and this
    assertion could never pass. It passing is what proves the reads go through the
    seam.
    """
    project = _make_project(Path("/proj"))
    result = ArtifactRead(path=Path("/proj/.factory/results/T89.json"), data={"status": "ready"})
    artifacts = FakeRunArtifactReader(results={"T89": result})
    service = RunService(FakeFileAdapter(project=project, tickets=[_make_ticket("T89")]), artifacts)

    (record,) = service.list_run_records(project)

    assert record.result.data == {"status": "ready"}, "the seeded artifact reached the record"
    assert record.result.reason is None
    # The unseeded source still answers with a NAMED reason rather than a bare blank,
    # and ``absent`` is what the real reader answers for a file that is not there.
    assert record.receipt.reason == "absent"
    assert record.receipt.data is None


def test_both_fake_sources_can_be_seeded_independently() -> None:
    """Seeding BOTH maps populates both fields — per source, like every absence assertion.

    ``results`` alone is not enough to pin the fake: ``read_result`` and
    ``read_receipt`` share one helper parameterized by map and directory, so a fake
    that returned the results map for both sources would satisfy a results-only test
    and hand every receipt assertion the wrong artifact.
    """
    project = _make_project(Path("/proj"))
    result = ArtifactRead(path=Path("/proj/.factory/results/T89.json"), data={"status": "ready"})
    receipt = ArtifactRead(path=Path("/proj/.factory/receipts/T89.json"), data={"verdict": "pass"})
    artifacts = FakeRunArtifactReader(results={"T89": result}, receipts={"T89": receipt})
    service = RunService(FakeFileAdapter(project=project, tickets=[_make_ticket("T89")]), artifacts)

    (record,) = service.list_run_records(project)

    assert record.result.data == {"status": "ready"}
    assert record.receipt.data == {"verdict": "pass"}, "the receipt map is not the result map"
    assert record.result.reason is None
    assert record.receipt.reason is None


@pytest.mark.parametrize("unsafe_id", [".", ".."])
def test_the_fake_refuses_a_path_unsafe_id_with_the_real_readers_reason(unsafe_id: str) -> None:
    """The fake answers ``unreadable`` for the ids the real reader refuses, not ``absent``.

    The fake's contract is that every reason it gives is one the real reader would
    give for the same id — otherwise a fake-backed test of exactly the case
    ``test_a_path_unsafe_manifest_id_degrades_only_its_own_record`` pins would pass
    green on ``absent``, claiming the factory wrote nothing when the console merely
    refused to look. Validating an id costs no I/O, so the fake can honour this
    without becoming filesystem-backed.
    """
    project = _make_project(Path("/proj"))
    service = RunService(
        FakeFileAdapter(project=project, tickets=[_make_ticket(unsafe_id)]),
        FakeRunArtifactReader(),
    )

    (record,) = service.list_run_records(project)

    assert record.result.reason == "unreadable"
    assert record.receipt.reason == "unreadable"
    assert record.result.data is None
    assert record.receipt.data is None
    # The bare-dot id forms an ordinary in-directory filename, so the fake names the
    # artifact — unresolved, per this fake's documented path divergence.
    assert record.result.path == Path("/proj") / RESULTS_RELATIVE_DIR / f"{unsafe_id}.json"


@pytest.mark.parametrize("escaping_id", ["../../etc/passwd", "/etc/passwd"])
def test_the_fake_never_reports_a_refusal_path_that_escapes_the_project(escaping_id: str) -> None:
    """The fake's refusal path is clamped like the real reader's, not a raw join.

    ``RealRunArtifactReader`` reports these through
    :func:`~factory_console.file_adapter.runs.refusal_path`, which clamps to the
    project. A fake that re-joined the id instead would hand a test
    ``/proj/.factory/results/../../etc/passwd.json`` — a path that RESOLVES outside the
    project, which the console provably never produces. The fake's paths are
    unresolved by design, but they must still be paths the real reader could name.
    """
    project = _make_project(Path("/proj"))
    artifacts = FakeRunArtifactReader()

    read = artifacts.read_result(project, escaping_id)

    assert read.reason == "unreadable"
    # The artifact DIRECTORY, with no component of the refused id in it — asserted as
    # an exact value rather than as "is inside /proj", which a raw join like
    # ``/proj/.factory/results/../../etc/passwd.json`` would satisfy lexically while
    # naming a file outside the project.
    assert read.path == Path("/proj") / RESULTS_RELATIVE_DIR
    assert escaping_id.strip("/") not in str(read.path)


def test_both_implementations_satisfy_the_port() -> None:
    # ``@runtime_checkable``, so structural conformance is assertable — the same
    # guarantee ``FakeFileAdapter``/``FileAdapter`` and ``FakeFileWriter``/``FileWriter``
    # already carry.
    assert isinstance(RealRunArtifactReader(), RunArtifactReader)
    assert isinstance(FakeRunArtifactReader(), RunArtifactReader)


# --------------------------------------------------------------------------- #
# GUARD: the read-only invariant on the new file_adapter module
# --------------------------------------------------------------------------- #


def test_run_artifacts_module_source_has_no_filesystem_mutation() -> None:
    assert_module_is_read_only(run_artifacts_module)


def test_run_artifacts_module_source_carries_the_read_only_header() -> None:
    assert_module_carries_read_only_header(run_artifacts_module)
