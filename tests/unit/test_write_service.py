"""Unit tests for :class:`WriteService` over an in-memory ``FileWriter`` + adapter.

Pin the service's create/edit/delete orchestration the write handlers delegate to:
the create-collision guard (:class:`WriteConflict`, both paths), the existence check
for edit/delete (:class:`TicketNotFound`, both paths), the writer's two run-state
mutability gates propagating (:class:`TicketNotMutable` — ``ensure_mutable`` on the
edit path, the wider ``ensure_deletable`` on the delete path), dry-run returning a
diff and committing
nothing, and an apply committing then re-reading the resulting ticket through the
adapter. Also covers the co-located error contract (:class:`WriteConflict` /
:class:`WriteValidationError` construction + envelope rendering).

Fixture note: the shipped :class:`FakeFileWriter` and :class:`FakeFileAdapter` hold
SEPARATE in-memory state, so a stock ``FakeFileAdapter``'s ``get_ticket`` would NOT
reflect a ``writer.create_ticket`` / ``edit_ticket`` apply — production works because
the real writer and adapter share ONE filesystem. To exercise the collision-then-
re-read behaviors with a single service, these tests use a tiny ``_StatefulAdapter``
whose ``get_ticket`` reads THROUGH the writer's live in-memory manifest/bodies (the
same state the writer's applies mutate), reproducing the shared-filesystem semantics
the two real classes share. It touches no filesystem.
"""

from datetime import datetime
from pathlib import Path

import pytest

from factory_console.domain import Project, RunState, Ticket
from factory_console.domain.write import DiffPreview, TicketDraft, TicketEdit, WriteResult
from factory_console.errors import to_error_response
from factory_console.file_adapter.fake_writer import FakeFileWriter
from factory_console.file_adapter.manifest import manifest_entry_to_ticket_stub
from factory_console.file_adapter.write_gate import TicketNotMutable
from factory_console.services.ticket_service import TicketNotFound
from factory_console.services.write_service import (
    WriteConflict,
    WriteService,
    WriteValidationError,
)

_MANIFEST_REL = "docs/planning/tickets.json"


# --------------------------------------------------------------------------- #
# Stateful test-double adapter — reads THROUGH the writer's live state
# --------------------------------------------------------------------------- #


class _StatefulAdapter:
    """A minimal ``FileAdapter`` whose ``get_ticket`` reflects the writer's applies.

    The stock ``FakeFileAdapter`` seeds its OWN ticket list, so it cannot observe a
    ``writer.create_ticket`` / ``edit_ticket`` / ``delete_ticket`` mutation — in
    production the real writer and adapter instead share one filesystem, so an
    adapter read after an apply sees the written state. This double reproduces that
    coupling for the tests by resolving ``get_ticket`` from the SAME in-memory
    manifest/bodies the writer mutates (reusing ``manifest_entry_to_ticket_stub`` —
    the canonical entry->Ticket mapper the writer itself uses), so a re-read after an
    apply reflects the change and a pre-create read reports a collision. ``WriteService``
    only ever calls ``has_ticket`` (its existence guards) and ``get_ticket`` (the
    post-write re-read), so no other ``FileAdapter`` method is implemented.
    """

    def __init__(self, writer: FakeFileWriter, project: Project) -> None:
        self._writer = writer
        self._project = project

    def has_ticket(self, project: Project, ticket_id: str) -> bool:
        """Manifest-only existence, exactly as the real adapter answers it."""
        return any(entry.get("id") == ticket_id for entry in self._writer._manifest)

    def get_ticket(self, project: Project, ticket_id: str) -> Ticket | None:
        for entry in self._writer._manifest:
            if entry.get("id") == ticket_id:
                stub = manifest_entry_to_ticket_stub(entry, project.ticketsDir)
                body = self._writer._bodies.get(ticket_id, "")
                run_state = self._writer._run_states.get(ticket_id, RunState.unknown)
                return stub.model_copy(update={"bodyMarkdown": body, "runState": run_state})
        return None


# --------------------------------------------------------------------------- #
# Seed helpers (mirrored from test_fake_writer.py)
# --------------------------------------------------------------------------- #


def _make_project(root: Path = Path("/proj"), *, with_roadmap: bool = True) -> Project:
    """Build a Project over in-memory-only paths (they need NOT exist on disk)."""
    return Project(
        rootPath=root,
        ticketsManifestPath=root / "docs" / "planning" / "tickets.json",
        ticketsDir=root / "docs" / "planning" / "tickets",
        roadmapPath=(root / "ROADMAP.md") if with_roadmap else None,
        runStateDir=root / ".factory" / "run-state",
        discoveredAt=datetime(2026, 7, 20, 12, 0, 0),
    )


def _entry(ticket_id: str, *, milestone: str | None = "MVP", **overrides: object) -> dict:
    base: dict = {
        "id": ticket_id,
        "title": f"Ticket {ticket_id}",
        "status": "todo",
        "track": "file-adapter",
        "milestone": milestone,
        "dependsOn": [],
        "provides": f"provides {ticket_id}",
        "files": [],
    }
    base.update(overrides)
    return base


def _draft(ticket_id: str = "TM-050", **overrides: object) -> TicketDraft:
    base: dict[str, object] = {
        "id": ticket_id,
        "title": "Ranger mobile capture",
        "track": "mobile",
        "milestone": "MVP",
        "dependsOn": ["TM-001"],
        "provides": "On-trail capture app",
        "files": ["server/trailmark/mobile/capture.py"],
        "bodyMarkdown": "# Capture\n\nBody text.\n",
    }
    base.update(overrides)
    return TicketDraft(**base)  # type: ignore[arg-type]


def _edit(**overrides: object) -> TicketEdit:
    base: dict[str, object] = {
        "title": "Ingest trail reports (v2)",
        "track": "ingestion",
        "milestone": "MVP",
        "dependsOn": [],
        "provides": "Nightly importer refreshed",
        "files": ["server/trailmark/ingest/csv_dropbox.py"],
        "bodyMarkdown": "# Ingest\n\nUpdated body.\n",
    }
    base.update(overrides)
    return TicketEdit(**base)  # type: ignore[arg-type]


def _service(
    run_states: dict[str, RunState] | None = None,
    *,
    root: Path = Path("/proj"),
    with_roadmap: bool = True,
) -> tuple[WriteService, Project, FakeFileWriter]:
    """A service over a writer seeded with TM-001 (todo) + TM-015, sharing one state.

    The ``_StatefulAdapter`` reads through the writer's in-memory manifest so both
    ports observe the same tickets — the shared-filesystem coupling the real pair has.
    """
    project = _make_project(root, with_roadmap=with_roadmap)
    writer = FakeFileWriter(
        manifest=[
            _entry("TM-001", title="Ingest trail reports", milestone="MVP"),
            _entry("TM-015", title="Public read API", milestone="v1"),
        ],
        bodies={"TM-001": "# TM-001 body\n", "TM-015": "# TM-015 body\n"},
        roadmap=("# Roadmap\n" if with_roadmap else None),
        run_states=run_states,
    )
    return WriteService(writer, _StatefulAdapter(writer, project)), project, writer


def _ids(writer: FakeFileWriter) -> list[str]:
    return [entry["id"] for entry in writer._manifest]


# --------------------------------------------------------------------------- #
# create — collision guard (both paths)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("dry_run", [True, False])
def test_create_on_existing_id_raises_write_conflict_and_commits_nothing(dry_run: bool) -> None:
    service, project, writer = _service()
    before = _ids(writer)

    with pytest.raises(WriteConflict) as exc_info:
        service.create(project, _draft(id="TM-001"), dry_run=dry_run)

    assert exc_info.value.code == "write_conflict"
    assert exc_info.value.status == 409
    # Nothing committed on either path.
    assert _ids(writer) == before


# --------------------------------------------------------------------------- #
# create — dry-run vs apply
# --------------------------------------------------------------------------- #


def test_create_dry_run_returns_preview_and_commits_nothing() -> None:
    service, project, writer = _service()
    before = _ids(writer)

    result = service.create(project, _draft(), dry_run=True)

    assert result.applied is False
    assert result.ticket is None
    assert result.ticketId == "TM-050"
    assert isinstance(result.diff, DiffPreview)
    assert result.changedFiles == [file.path for file in result.diff.files]
    assert _MANIFEST_REL in result.changedFiles
    # Writer state untouched: nothing was created.
    assert _ids(writer) == before


def test_create_apply_commits_and_reread_reflects_created_state() -> None:
    service, project, writer = _service()

    result = service.create(
        project, _draft(bodyMarkdown="# Capture\n\nFresh body.\n"), dry_run=False
    )

    assert result.applied is True
    assert result.ticket is not None
    # The ticket carried is the re-read (via the adapter over the writer's live state).
    assert result.ticket.id == "TM-050"
    assert result.ticket.title == "Ranger mobile capture"
    assert result.ticket.bodyMarkdown == "# Capture\n\nFresh body.\n"
    # It was actually committed to the writer's manifest.
    assert "TM-050" in _ids(writer)


# --------------------------------------------------------------------------- #
# edit — existence check, gate, dry-run vs apply
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("dry_run", [True, False])
def test_edit_absent_id_raises_ticket_not_found(dry_run: bool) -> None:
    service, project, _writer = _service()
    with pytest.raises(TicketNotFound) as exc_info:
        service.edit(project, "TM-999", _edit(), dry_run=dry_run)
    assert exc_info.value.code == "ticket_not_found"
    assert exc_info.value.status == 404


@pytest.mark.parametrize("state", [RunState.in_flight, RunState.ready, RunState.merged])
def test_edit_apply_on_non_mutable_state_raises_not_mutable_and_writes_nothing(
    state: RunState,
) -> None:
    service, project, writer = _service(run_states={"TM-001": state})
    before_body = writer._bodies["TM-001"]

    with pytest.raises(TicketNotMutable) as exc_info:
        service.edit(project, "TM-001", _edit(), dry_run=False)

    assert exc_info.value.code == "ticket_not_mutable"
    assert exc_info.value.status == 409
    # Gate fires before any mutation.
    assert writer._bodies["TM-001"] == before_body


def test_edit_dry_run_on_todo_returns_diff_and_commits_nothing() -> None:
    service, project, writer = _service(run_states={"TM-001": RunState.todo})
    before_body = writer._bodies["TM-001"]

    result = service.edit(project, "TM-001", _edit(title="Ingest trail reports (v2)"), dry_run=True)

    assert result.applied is False
    assert result.ticket is None
    assert result.ticketId == "TM-001"
    assert isinstance(result.diff, DiffPreview)
    assert writer._bodies["TM-001"] == before_body


def test_edit_apply_on_todo_commits_and_reread_reflects_edit() -> None:
    service, project, writer = _service(run_states={"TM-001": RunState.todo})

    result = service.edit(
        project,
        "TM-001",
        _edit(title="Ingest trail reports (v2)", bodyMarkdown="# Ingest\n\nCommitted body.\n"),
        dry_run=False,
    )

    assert result.applied is True
    assert result.ticket is not None
    assert result.ticket.title == "Ingest trail reports (v2)"
    assert result.ticket.bodyMarkdown == "# Ingest\n\nCommitted body.\n"
    # The write landed in the writer's live state.
    assert writer._bodies["TM-001"] == "# Ingest\n\nCommitted body.\n"


# --------------------------------------------------------------------------- #
# delete — existence check, gate, dry-run vs apply
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("dry_run", [True, False])
def test_delete_absent_id_raises_ticket_not_found(dry_run: bool) -> None:
    service, project, _writer = _service()
    with pytest.raises(TicketNotFound) as exc_info:
        service.delete(project, "TM-999", dry_run=dry_run)
    assert exc_info.value.code == "ticket_not_found"
    assert exc_info.value.status == 404


@pytest.mark.parametrize("state", [RunState.in_flight, RunState.ready, RunState.merged])
def test_delete_apply_on_non_mutable_state_raises_not_mutable_and_writes_nothing(
    state: RunState,
) -> None:
    service, project, writer = _service(run_states={"TM-001": state})
    before = _ids(writer)

    with pytest.raises(TicketNotMutable):
        service.delete(project, "TM-001", dry_run=False)

    assert _ids(writer) == before


def test_delete_dry_run_on_todo_returns_diff_and_commits_nothing() -> None:
    service, project, writer = _service(run_states={"TM-001": RunState.todo})
    before = _ids(writer)

    result = service.delete(project, "TM-001", dry_run=True)

    assert result.applied is False
    assert result.ticket is None
    assert result.ticketId == "TM-001"
    assert isinstance(result.diff, DiffPreview)
    assert _ids(writer) == before


def test_delete_apply_on_todo_commits_with_snapshot_ticket() -> None:
    service, project, writer = _service(run_states={"TM-001": RunState.todo})

    result = service.delete(project, "TM-001", dry_run=False)

    assert result.applied is True
    # Delete does NOT re-read (the ticket is gone) — the writer's snapshot is returned.
    assert result.ticket is not None
    assert result.ticket.id == "TM-001"
    # Gone from the writer's live state.
    assert "TM-001" not in _ids(writer)


# --------------------------------------------------------------------------- #
# Co-located error contract — construction + envelope rendering
# --------------------------------------------------------------------------- #


def test_write_conflict_construction_and_envelope() -> None:
    exc = WriteConflict("TM-001")
    assert exc.code == "write_conflict"
    assert exc.status == 409
    assert exc.message == "Ticket 'TM-001' already exists"
    assert exc.details == {"ticketId": "TM-001"}
    assert to_error_response(exc) == {
        "error": {
            "code": "write_conflict",
            "message": "Ticket 'TM-001' already exists",
            "details": {"ticketId": "TM-001"},
        }
    }


def test_write_validation_error_construction_and_envelope() -> None:
    exc = WriteValidationError("bad request body", details={"field": "id"})
    assert exc.code == "write_validation_error"
    assert exc.status == 422
    assert exc.message == "bad request body"
    assert exc.details == {"field": "id"}
    assert to_error_response(exc) == {
        "error": {
            "code": "write_validation_error",
            "message": "bad request body",
            "details": {"field": "id"},
        }
    }


def test_write_validation_error_omits_details_when_none() -> None:
    exc = WriteValidationError("bad request body")
    assert exc.details is None
    assert to_error_response(exc) == {
        "error": {"code": "write_validation_error", "message": "bad request body"}
    }


def test_apply_result_is_write_result_envelope() -> None:
    service, project, _writer = _service()
    result = service.create(project, _draft(), dry_run=False)
    assert isinstance(result, WriteResult)
