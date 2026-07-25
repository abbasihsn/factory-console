"""Unit tests for the in-memory :class:`FakeFileWriter`.

These pin the write-path ``FileWriter`` contract the backend codes against before
the real writer exists: the ``@runtime_checkable`` ``isinstance`` gate, the three
apply flows (create/edit/delete) mutating only in-memory seeded state, the
todo-only mutability gate over the seeded run-state, the pure ``preview_*`` diffs,
and — the load-bearing guarantee — that NOTHING ever touches the filesystem
(proven both by the shared AST guard and behaviorally against an empty
``tmp_path``). Deterministic and I/O-free — pydantic + stdlib only.
"""

from datetime import datetime
from pathlib import Path

import pytest
from _read_only_guard import assert_module_is_read_only  # top-level test helper

from factory_console.domain import Project, RunState
from factory_console.domain.ticket import Ticket
from factory_console.domain.write import DiffPreview, TicketDraft, TicketEdit, WriteResult
from factory_console.file_adapter import fake_writer as fake_writer_module
from factory_console.file_adapter.fake_writer import FakeFileWriter
from factory_console.file_adapter.write_gate import TicketNotMutable
from factory_console.file_adapter.write_render import TicketAlreadyExists, UnknownTicket
from factory_console.file_adapter.writer_protocol import FileWriter


class _PartialWriter:
    """Implements only ONE of the six methods — proves the runtime check is real."""

    def preview_create(  # pragma: no cover - never called
        self, project: Project, draft: TicketDraft
    ) -> DiffPreview:
        raise NotImplementedError


_MANIFEST_REL = "docs/planning/tickets.json"
_ROADMAP_REL = "ROADMAP.md"

_ROADMAP = (
    "# TrailMark Roadmap\n"
    "\n"
    "## MVP — make ranger reports usable\n"
    "\n"
    "- [x] Canonical trail-report schema and store\n"
    "- [ ] Ingest trail reports (TM-001)\n"
    "\n"
    "## v1 — put conditions in front of hikers\n"
    "\n"
    "- [ ] Public read API (TM-015)\n"
)


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


def _seeded_writer(
    run_states: dict[str, RunState] | None = None, *, roadmap: str | None = _ROADMAP
) -> FakeFileWriter:
    """A writer seeded with TM-001 (under ## MVP) and TM-015 (under ## v1)."""
    return FakeFileWriter(
        manifest=[
            _entry("TM-001", title="Ingest trail reports", milestone="MVP"),
            _entry("TM-015", title="Public read API", milestone="v1"),
        ],
        bodies={"TM-001": "# TM-001 body\n", "TM-015": "# TM-015 body\n"},
        roadmap=roadmap,
        run_states=run_states,
    )


# --------------------------------------------------------------------------- #
# runtime_checkable Protocol gate
# --------------------------------------------------------------------------- #


def test_fake_satisfies_runtime_checkable_file_writer() -> None:
    assert isinstance(FakeFileWriter(manifest=[]), FileWriter)


def test_object_without_the_six_methods_is_not_a_file_writer() -> None:
    # The runtime check is real, not vacuous: a bare object and a partial
    # implementation (missing five of the six methods) are both rejected.
    assert not isinstance(object(), FileWriter)
    assert not isinstance(_PartialWriter(), FileWriter)


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #


def test_create_adds_in_memory_ticket_with_three_coupled_files() -> None:
    writer = _seeded_writer()
    project = _make_project()

    result = writer.create_ticket(project, _draft(milestone="MVP"))

    assert result.applied is True
    assert result.ticketId == "TM-050"
    assert result.ticket is not None
    assert result.ticket.id == "TM-050"
    # A matching ## MVP roadmap section is seeded, so all three coupled paths change.
    assert result.changedFiles == [
        _MANIFEST_REL,
        "docs/planning/tickets/TM-050.md",
        _ROADMAP_REL,
    ]
    # changedFiles always agrees with the diff it carries.
    assert result.changedFiles == [file.path for file in result.diff.files]


def test_create_reflected_in_subsequent_in_memory_state() -> None:
    writer = _seeded_writer()
    project = _make_project()
    writer.create_ticket(project, _draft())

    # A follow-up preview sees the new ticket: the manifest diff no longer creates
    # it (it is already present), and a re-create is now a duplicate.
    preview = writer.preview_edit(project, "TM-050", _edit(milestone="MVP"))
    assert isinstance(preview, DiffPreview)
    with pytest.raises(TicketAlreadyExists):
        writer.create_ticket(project, _draft())


def test_create_duplicate_id_raises_already_exists() -> None:
    writer = _seeded_writer()
    with pytest.raises(TicketAlreadyExists) as exc_info:
        writer.create_ticket(_make_project(), _draft(id="TM-001"))
    assert exc_info.value.status == 409
    assert exc_info.value.details == {"ticketId": "TM-001"}


# --------------------------------------------------------------------------- #
# front-matter fidelity — the fake's .md diff must match the real writer's
# --------------------------------------------------------------------------- #


def test_edit_of_front_matter_ticket_diffs_only_the_body_not_the_fence() -> None:
    # Regression: the .md diff's currentText is the FULL rendered file (YAML fence +
    # body) via the same write_render._render_md the real writer reads off disk — so
    # editing only the body of a front-matter ticket diffs the body alone and never
    # shows the fence spuriously re-added (which body-only currentText would).
    front = {"status": "draft", "owner": "ranger"}
    writer = FakeFileWriter(
        manifest=[_entry("TM-001", milestone="MVP")],
        bodies={"TM-001": "# Old body\n"},
        front_matter={"TM-001": front},
        roadmap=None,
    )
    project = _make_project(with_roadmap=False)

    preview = writer.preview_edit(
        project,
        "TM-001",
        _edit(
            title="Ticket TM-001",
            track="file-adapter",
            milestone="MVP",
            dependsOn=[],
            provides="provides TM-001",
            files=[],
            frontMatter=front,
            bodyMarkdown="# New body\n",
        ),
    )

    md = next(f for f in preview.files if f.path == "docs/planning/tickets/TM-001.md")
    assert md.changeKind == "modify"
    # The fence + front-matter lines are unchanged context, never additions/removals.
    assert "+status: draft" not in md.diff
    assert "+---" not in md.diff
    # Only the body line changed.
    assert "-# Old body" in md.diff
    assert "+# New body" in md.diff


def test_create_then_preview_identical_edit_is_a_md_noop_for_front_matter_ticket() -> None:
    # After a create "writes" front-matter into the fake, re-previewing the identical
    # edit is a genuine .md no-op (omitted from the preview) — exactly what the real
    # disk-backed writer computes. Before tracking front-matter, currentText was
    # body-only and the fence would re-appear as an addition, so the .md would show.
    writer = _seeded_writer(roadmap=None)
    project = _make_project(with_roadmap=False)
    front = {"status": "draft", "owner": "ranger"}
    writer.create_ticket(project, _draft(frontMatter=front, milestone="MVP"))

    preview = writer.preview_edit(
        project,
        "TM-050",
        _edit(
            title="Ranger mobile capture",
            track="mobile",
            milestone="MVP",
            dependsOn=["TM-001"],
            provides="On-trail capture app",
            files=["server/trailmark/mobile/capture.py"],
            frontMatter=front,
            bodyMarkdown="# Capture\n\nBody text.\n",
        ),
    )

    md_paths = [f.path for f in preview.files if f.path == "docs/planning/tickets/TM-050.md"]
    assert md_paths == []  # unchanged .md → omitted, no spurious fence diff


def test_create_without_matching_roadmap_section_changes_two_files() -> None:
    writer = _seeded_writer()
    result = writer.create_ticket(_make_project(), _draft(milestone="v99-nonexistent"))
    assert result.changedFiles == [_MANIFEST_REL, "docs/planning/tickets/TM-050.md"]


def test_create_with_no_roadmap_seeded_changes_two_files() -> None:
    writer = _seeded_writer(roadmap=None)
    project = _make_project(with_roadmap=False)
    result = writer.create_ticket(project, _draft())
    assert result.changedFiles == [_MANIFEST_REL, "docs/planning/tickets/TM-050.md"]


# --------------------------------------------------------------------------- #
# edit — mutability gate + merge
# --------------------------------------------------------------------------- #


def test_edit_on_todo_ticket_updates_and_returns_applied() -> None:
    writer = _seeded_writer(run_states={"TM-001": RunState.todo})
    project = _make_project()

    result = writer.edit_ticket(project, "TM-001", _edit(title="Ingest trail reports (v2)"))

    assert result.applied is True
    assert result.ticket is not None
    assert result.ticket.title == "Ingest trail reports (v2)"
    assert result.ticket.bodyMarkdown == "# Ingest\n\nUpdated body.\n"
    # The entry was merged in place: a re-preview against a DIFFERENT body renders
    # the now-current "Updated body" on the removed side of the .md diff.
    reprev = writer.preview_edit(project, "TM-001", _edit(bodyMarkdown="# Ingest\n\nEven newer.\n"))
    md = next(f for f in reprev.files if f.path.endswith("TM-001.md"))
    assert "Updated body" in md.diff
    assert "Even newer" in md.diff


def test_edit_on_unknown_run_state_is_allowed() -> None:
    # No seeded run-state -> unknown, which is mutable (mirrors ensure_mutable).
    writer = _seeded_writer(run_states=None)
    result = writer.edit_ticket(_make_project(), "TM-001", _edit())
    assert result.applied is True


@pytest.mark.parametrize("state", [RunState.in_flight, RunState.ready, RunState.merged])
def test_edit_on_non_mutable_state_raises_not_mutable(state: RunState) -> None:
    writer = _seeded_writer(run_states={"TM-001": state})
    with pytest.raises(TicketNotMutable) as exc_info:
        writer.edit_ticket(_make_project(), "TM-001", _edit())
    assert exc_info.value.status == 409
    assert exc_info.value.details == {"ticketId": "TM-001", "runState": state.value}


@pytest.mark.parametrize("state", [RunState.in_flight, RunState.ready, RunState.merged])
def test_delete_on_non_mutable_state_raises_not_mutable(state: RunState) -> None:
    writer = _seeded_writer(run_states={"TM-001": state})
    with pytest.raises(TicketNotMutable):
        writer.delete_ticket(_make_project(), "TM-001")


def test_edit_unknown_id_raises_unknown_ticket() -> None:
    writer = _seeded_writer()
    with pytest.raises(UnknownTicket) as exc_info:
        writer.edit_ticket(_make_project(), "TM-999", _edit())
    assert exc_info.value.status == 404
    assert exc_info.value.details == {"ticketId": "TM-999"}


def test_delete_unknown_id_raises_unknown_ticket() -> None:
    writer = _seeded_writer()
    with pytest.raises(UnknownTicket) as exc_info:
        writer.delete_ticket(_make_project(), "TM-999")
    assert exc_info.value.status == 404


# --------------------------------------------------------------------------- #
# preview — pure unified diffs, no mutation
# --------------------------------------------------------------------------- #


def test_preview_edit_carries_unified_diff_and_does_not_mutate() -> None:
    writer = _seeded_writer(run_states={"TM-001": RunState.todo})
    project = _make_project()

    preview = writer.preview_edit(project, "TM-001", _edit(title="Ingest trail reports (v2)"))

    assert isinstance(preview, DiffPreview)
    assert preview.ticketId == "TM-001"
    manifest_diff = next(f for f in preview.files if f.path == _MANIFEST_REL)
    # A real unified diff carries the ---/+++/@@ hunk markers.
    assert "@@" in manifest_diff.diff
    assert "--- a/" in manifest_diff.diff
    assert "+++ b/" in manifest_diff.diff

    # Preview mutated nothing: a second identical preview yields the identical diff
    # (idempotent -> the seeded state was untouched), and the current side still
    # shows the ORIGINAL title as the removed line.
    again = writer.preview_edit(project, "TM-001", _edit(title="Ingest trail reports (v2)"))
    assert again == preview
    assert '-      "title": "Ingest trail reports"' in manifest_diff.diff


# --------------------------------------------------------------------------- #
# delete
# --------------------------------------------------------------------------- #


def test_delete_removes_in_memory_ticket_and_returns_snapshot() -> None:
    writer = _seeded_writer(run_states={"TM-001": RunState.todo})
    project = _make_project()

    result = writer.delete_ticket(project, "TM-001")

    assert result.applied is True
    assert result.ticket is not None
    # The returned Ticket is the deleted ticket's final snapshot.
    assert result.ticket.id == "TM-001"
    # It is gone from in-memory state: any subsequent op is an unknown ticket.
    for op in (
        lambda: writer.preview_edit(project, "TM-001", _edit()),
        lambda: writer.edit_ticket(project, "TM-001", _edit()),
        lambda: writer.delete_ticket(project, "TM-001"),
    ):
        with pytest.raises(UnknownTicket):
            op()


# --------------------------------------------------------------------------- #
# Purity — the fake never touches the filesystem
# --------------------------------------------------------------------------- #


def test_module_is_read_only_by_ast_guard() -> None:
    assert_module_is_read_only(fake_writer_module)


def test_apply_flows_write_nothing_to_disk(tmp_path: Path) -> None:
    # Behavioral purity: rooted at an EMPTY tmp_path with no files seeded on disk,
    # a full create/edit/delete cycle creates NOTHING under the root.
    project = _make_project(tmp_path)
    writer = _seeded_writer(run_states={"TM-001": RunState.todo})

    writer.create_ticket(project, _draft())
    writer.edit_ticket(project, "TM-001", _edit())
    writer.delete_ticket(project, "TM-001")

    assert list(tmp_path.rglob("*")) == []


def test_ticket_carries_seeded_run_state_and_provides_list() -> None:
    # The returned Ticket is a well-formed domain Ticket: provides coerced to a
    # list and run-state carried from the seeded map.
    writer = _seeded_writer(run_states={"TM-001": RunState.todo})
    result = writer.edit_ticket(_make_project(), "TM-001", _edit())
    assert isinstance(result.ticket, Ticket)
    assert result.ticket.runState is RunState.todo
    assert result.ticket.provides == ["Nightly importer refreshed"]
    assert isinstance(result, WriteResult)
