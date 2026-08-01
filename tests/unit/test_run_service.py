"""Unit tests for :class:`RunService` over the two in-memory ports.

The sibling of :mod:`tests.unit.test_ticket_service` and friends: the manifest and
run-state come from :class:`FakeFileAdapter`, the run artifacts from
:class:`FakeRunArtifactReader`, so nothing here touches a filesystem and every
case is stated as data rather than as files placed on disk.

What this pins is :meth:`RunService._compose`'s branching — which sources land in
``unavailable``, and why — because that is the endpoint's whole contract and the
one thing the file-adapter tests (:mod:`tests.unit.test_runs`, which cover
reading the artifacts) cannot reach. :mod:`tests.integration.test_api_runs`
covers the same ground end-to-end over real files; this covers it in isolation,
where a regression names the branch that broke instead of an HTTP body that
changed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from factory_console.domain import Project, RunState, RunStateSource, Ticket
from factory_console.domain.run_record import LastStop, RunResultSummary
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.fake_runs import FakeRunArtifactReader
from factory_console.file_adapter.runs_protocol import RunArtifactReader
from factory_console.services.run_service import RunService
from factory_console.services.ticket_service import TicketNotFound

TICKET_IDS = ["T-100", "T-118", "T-125"]


def _project(*, source_kind: str | None = "json") -> Project:
    """A project whose run-state artifact is the JSON form, the marker dir, or absent."""
    run_state_source = (
        None
        if source_kind is None
        else RunStateSource(
            kind="json" if source_kind == "json" else "directory",
            path=Path("/proj/.factory/run-state.json")
            if source_kind == "json"
            else Path("/proj/.factory/run-state"),
        )
    )
    return Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        discoveredAt=datetime(2026, 7, 21, 12, 0, 0),
        runStateSource=run_state_source,
    )


def _ticket(ticket_id: str) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=f"Ticket {ticket_id}",
        status="todo",
        track="backend",
        milestone="MVP",
        filePath=Path(f"/proj/docs/planning/tickets/{ticket_id}.md"),
        bodyMarkdown=f"# {ticket_id}",
        bodyHtml=f"<h1>{ticket_id}</h1>",
        raw={"id": ticket_id},
    )


def _service(
    reader: RunArtifactReader | None = None,
    *,
    run_states: dict[str, RunState] | None = None,
    project: Project | None = None,
) -> tuple[RunService, Project]:
    resolved = project if project is not None else _project()
    adapter = FakeFileAdapter(
        project=resolved,
        tickets=[_ticket(ticket_id) for ticket_id in TICKET_IDS],
        run_states=run_states,
    )
    return RunService(adapter, reader or FakeRunArtifactReader()), resolved


def test_the_real_and_fake_readers_both_satisfy_the_port() -> None:
    from factory_console.file_adapter.real_runs import RealRunArtifactReader

    assert isinstance(FakeRunArtifactReader(), RunArtifactReader)
    assert isinstance(RealRunArtifactReader(), RunArtifactReader)


# --------------------------------------------------------------------------- #
# list_records — one record per manifest ticket, in manifest order
# --------------------------------------------------------------------------- #


def test_records_are_one_per_manifest_ticket_in_manifest_order() -> None:
    service, project = _service()

    assert [record.ticketId for record in service.list_records(project)] == TICKET_IDS


def test_a_result_for_an_id_the_manifest_does_not_name_contributes_no_record() -> None:
    # The list is bounded by the MANIFEST, per the ticket's NFR: an artifact
    # naming an unknown id must not conjure a record for it.
    reader = FakeRunArtifactReader(
        results={"GHOST-1": RunResultSummary(status="ready")},
        receipts=["GHOST-1"],
        pr_urls={"GHOST-1": "https://example.test/pull/9"},
    )
    service, project = _service(reader)

    assert [record.ticketId for record in service.list_records(project)] == TICKET_IDS


def test_every_source_answering_leaves_unavailable_empty() -> None:
    reader = FakeRunArtifactReader(
        results={"T-100": RunResultSummary(status="ready")},
        receipts=["T-100"],
        pr_urls={"T-100": "https://example.test/pull/1"},
    )
    service, project = _service(reader, run_states={"T-100": RunState.merged})

    record = service.list_records(project)[0]

    assert record.ticketId == "T-100"
    assert record.unavailable == []
    assert record.runState is RunState.merged
    assert record.prUrl == "https://example.test/pull/1"
    assert record.hasReceipt is True


def test_each_silent_source_is_named_in_unavailable() -> None:
    # Nothing seeded at all: every per-ticket source is silent, and the record
    # says so rather than presenting three unexplained nulls.
    service, project = _service()

    record = service.list_records(project)[0]

    assert record.unavailable == ["runState", "results", "receipts"]
    assert record.runState is RunState.unknown
    assert record.prUrl is None
    assert record.result is None
    assert record.hasReceipt is False


def test_a_known_state_with_no_artifacts_names_only_results_and_receipts() -> None:
    service, project = _service(run_states={"T-100": RunState.ready})

    record = service.list_records(project)[0]

    assert record.unavailable == ["results", "receipts"]


def test_last_stop_is_never_named_in_a_record() -> None:
    # ``lastStop`` is a PROJECT-level fact reported once by the list endpoint's
    # ``sources``; a record claiming a per-ticket last-stop would invent one.
    reader = FakeRunArtifactReader(last_stop=LastStop(reason="merge conflict"))
    service, project = _service(reader)

    for record in service.list_records(project):
        assert "lastStop" not in record.unavailable


# --------------------------------------------------------------------------- #
# The run-state FORM decides whether a null prUrl is attributable
# --------------------------------------------------------------------------- #


def test_a_marker_directory_project_names_run_state_for_its_null_pr_urls() -> None:
    # Only the JSON form carries PR urls, so on a marker-directory project every
    # ``prUrl`` is null no matter what the factory did. Left unnamed that reads as
    # "no PR was opened" — a fact — instead of "this form cannot tell you".
    service, project = _service(
        run_states={"T-100": RunState.merged}, project=_project(source_kind="directory")
    )

    record = service.list_records(project)[0]

    assert record.prUrl is None
    assert "runState" in record.unavailable


def test_a_json_project_with_a_known_state_and_no_pr_does_not_name_run_state() -> None:
    # The JSON form CAN carry a url and simply does not for this ticket, so the
    # null is the fact "no PR yet" and the source stays unnamed.
    service, project = _service(run_states={"T-100": RunState.ready})

    record = service.list_records(project)[0]

    assert record.prUrl is None
    assert "runState" not in record.unavailable


def test_a_hostile_pr_url_from_run_state_is_dropped_from_the_record() -> None:
    # The url reaches the record straight out of ``run-state.json``, a file
    # ANOTHER process writes, and exists to become an ``href`` in the console —
    # the page that holds the write token. A non-http(s) scheme is refused at the
    # domain boundary rather than left for the SPA to re-check.
    reader = FakeRunArtifactReader(pr_urls={"T-100": "javascript:alert(1)"})
    service, project = _service(reader, run_states={"T-100": RunState.merged})

    record = service.list_records(project)[0]

    assert record.prUrl is None
    assert record.runState is RunState.merged, "the rest of the record must survive"


def test_an_unparseable_pr_url_is_dropped_rather_than_failing_the_request() -> None:
    # ``urlsplit`` RAISES on an unbalanced ``[`` in the authority. Unguarded, the
    # clearest case of a bad url there is — one that cannot even be parsed —
    # would escape as a ValidationError and 500 BOTH runs endpoints for the whole
    # project until the factory rewrote the file. It is dropped by the same rule
    # that drops a ``javascript:`` url: a value that cannot be parsed cannot be
    # shown to carry an allowed scheme.
    reader = FakeRunArtifactReader(pr_urls={"T-100": "https://exa[mple.test/pull/1"})
    service, project = _service(reader, run_states={"T-100": RunState.merged})

    record = service.list_records(project)[0]

    assert record.prUrl is None
    assert record.runState is RunState.merged, "the rest of the record must survive"


def test_a_project_with_no_run_state_artifact_names_run_state() -> None:
    service, project = _service(project=_project(source_kind=None))

    record = service.list_records(project)[0]

    assert "runState" in record.unavailable


# --------------------------------------------------------------------------- #
# get_record — membership is the MANIFEST's answer
# --------------------------------------------------------------------------- #


def test_get_record_returns_the_record_for_a_manifest_ticket() -> None:
    reader = FakeRunArtifactReader(receipts=["T-118"])
    service, project = _service(reader, run_states={"T-118": RunState.ready})

    record = service.get_record(project, "T-118")

    assert record.ticketId == "T-118"
    assert record.hasReceipt is True
    assert record.runState is RunState.ready


def test_get_record_raises_the_shared_not_found_for_an_unknown_id() -> None:
    service, project = _service()

    with pytest.raises(TicketNotFound) as excinfo:
        service.get_record(project, "NOPE-1")

    # The SAME class the ticket and deps services raise, so one ``except
    # TicketNotFound`` at the edge catches every "no such ticket".
    assert excinfo.value.code == "ticket_not_found"
    assert excinfo.value.status == 404


def test_a_manifest_ticket_absent_from_run_state_is_a_record_not_a_404() -> None:
    service, project = _service()

    record = service.get_record(project, "T-125")

    assert record.runState is RunState.unknown
    assert "runState" in record.unavailable


def test_list_and_detail_agree_about_the_same_ticket() -> None:
    reader = FakeRunArtifactReader(
        results={"T-118": RunResultSummary(status="ready")},
        receipts=["T-118"],
        pr_urls={"T-118": "https://example.test/pull/2"},
    )
    service, project = _service(reader, run_states={"T-118": RunState.merged})

    from_list = next(r for r in service.list_records(project) if r.ticketId == "T-118")

    assert service.get_record(project, "T-118") == from_list


# --------------------------------------------------------------------------- #
# The project-level reads pass straight through
# --------------------------------------------------------------------------- #


def test_source_paths_and_last_stop_come_from_the_reader() -> None:
    sources = {
        "runState": Path("/proj/.factory/run-state.json"),
        "results": Path("/proj/.factory/results"),
        "receipts": None,
        "lastStop": None,
    }
    reader = FakeRunArtifactReader(sources=sources, last_stop=LastStop(reason="stopped"))
    service, project = _service(reader)

    assert service.source_paths(project) == sources
    assert service.read_last_stop(project) == LastStop(reason="stopped")
