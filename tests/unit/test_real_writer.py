"""Unit tests for the filesystem-backed :class:`RealFileWriter`.

Fast but genuinely disk-backed: each test copies the ``with_run_state`` fixture
into a fresh ``tmp_path`` (so the checked-in fixture is never mutated) and drives
the real writer against it, re-reading through the production
:class:`~factory_console.file_adapter.real.RealFileAdapter`. These pin the write
contract end-to-end — the ``@runtime_checkable`` ``isinstance`` gate, the three
apply flows over real files, the todo-only mutability gate, pure previews, and the
load-bearing safety invariant that a refused write leaves every byte untouched.

Deterministic and dependency-light: pydantic + PyYAML + stdlib only, no network,
no hypothesis.
"""

import hashlib
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from factory_console.domain import Project, RunState
from factory_console.domain.ticket import Ticket
from factory_console.domain.write import DiffPreview, TicketDraft, TicketEdit, WriteResult
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.real import RealFileAdapter
from factory_console.file_adapter.real_writer import RealFileWriter
from factory_console.file_adapter.write_gate import TicketNotMutable
from factory_console.file_adapter.write_render import TicketAlreadyExists, UnknownTicket
from factory_console.file_adapter.writer_protocol import FileWriter

WITH_RUN_STATE = Path(__file__).resolve().parents[1] / "fixtures" / "projects" / "with_run_state"

_MANIFEST_REL = "docs/planning/tickets.json"
_ROADMAP_REL = "ROADMAP.md"

# Fixture ticket ids grouped by mutability (see the fixture ROADMAP run-state note).
_TODO_IDS = ["CAD-131", "CAD-140", "CAD-152"]
_NON_MUTABLE = {
    "CAD-125": RunState.in_flight,
    "CAD-118": RunState.ready,
    "CAD-100": RunState.merged,
}


class _PartialWriter:
    """Implements only ONE of the six methods — proves the runtime check is real."""

    def preview_create(  # pragma: no cover - never called
        self, project: Project, draft: TicketDraft
    ) -> DiffPreview:
        raise NotImplementedError


def _load(tmp_path: Path) -> tuple[RealFileWriter, Project]:
    """Copy the fixture into ``tmp_path`` and return a writer + loaded project."""
    root = tmp_path / "project"
    shutil.copytree(WITH_RUN_STATE, root)
    project = RealFileAdapter().load_project(root)
    return RealFileWriter(), project


def _load_without_run_state(tmp_path: Path) -> tuple[RealFileWriter, Project]:
    """Like :func:`_load`, but with ``.factory/`` stripped: no run-state source at all."""
    root = tmp_path / "project"
    shutil.copytree(WITH_RUN_STATE, root)
    shutil.rmtree(root / ".factory")
    project = RealFileAdapter().load_project(root)
    return RealFileWriter(), project


def _hash_tree(root: Path) -> dict[str, str]:
    """Map every file under ``root`` (project-relative POSIX) to its content SHA-256."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _draft(ticket_id: str = "CAD-200", **overrides: object) -> TicketDraft:
    base: dict[str, object] = {
        "id": ticket_id,
        "title": "Slack check-in integration",
        "track": "integrations",
        "milestone": "v2",
        "dependsOn": ["CAD-152"],
        "provides": "Check in to a habit straight from a Slack slash command",
        "files": ["server/cadence/integrations/slack.py"],
        "bodyMarkdown": "# Slack check-in\n\nBody text.\n",
    }
    base.update(overrides)
    return TicketDraft(**base)  # type: ignore[arg-type]


def _edit(**overrides: object) -> TicketEdit:
    base: dict[str, object] = {
        "title": "Weekly digest email (revised)",
        "track": "notifications",
        "milestone": "v1",
        "dependsOn": ["CAD-125"],
        "provides": "Monday-morning per-member digest, revised",
        "files": ["server/cadence/notifications/weekly_digest.py"],
        "bodyMarkdown": "# Weekly digest email\n\nRevised body.\n",
    }
    base.update(overrides)
    return TicketEdit(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# runtime_checkable Protocol gate
# --------------------------------------------------------------------------- #


def test_real_writer_satisfies_runtime_checkable_file_writer() -> None:
    assert isinstance(RealFileWriter(), FileWriter)


def test_object_without_the_six_methods_is_not_a_file_writer() -> None:
    # The runtime check is real, not vacuous: a bare object and a one-method partial
    # are both rejected.
    assert not isinstance(object(), FileWriter)
    assert not isinstance(_PartialWriter(), FileWriter)


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #


def test_create_writes_three_coupled_files_and_is_readable(tmp_path: Path) -> None:
    writer, project = _load(tmp_path)

    result = writer.create_ticket(project, _draft(milestone="v2"))

    assert isinstance(result, WriteResult)
    assert result.applied is True
    assert result.ticketId == "CAD-200"
    assert isinstance(result.ticket, Ticket)
    assert result.ticket.id == "CAD-200"
    # A matching ## v2 roadmap section exists, so all three coupled paths change.
    assert result.changedFiles == [
        _MANIFEST_REL,
        "docs/planning/tickets/CAD-200.md",
        _ROADMAP_REL,
    ]
    # changedFiles always agrees with the diff it carries.
    assert result.changedFiles == [file.path for file in result.diff.files]

    # The files landed on disk and the real read adapter now lists and reads them.
    adapter = RealFileAdapter()
    assert (project.ticketsDir / "CAD-200.md").is_file()
    assert "CAD-200" in {s.id for s in adapter.list_tickets(project)}
    reread = adapter.get_ticket(project, "CAD-200")
    assert reread is not None
    assert reread.title == "Slack check-in integration"
    assert reread.bodyMarkdown == "# Slack check-in\n\nBody text.\n"
    # The new roadmap line was inserted under ## v2.
    assert "**CAD-200** — Slack check-in integration" in project.roadmapPath.read_text()


def test_create_duplicate_id_raises_already_exists_and_writes_nothing(tmp_path: Path) -> None:
    writer, project = _load(tmp_path)
    before = _hash_tree(project.rootPath)

    with pytest.raises(TicketAlreadyExists) as exc_info:
        writer.create_ticket(project, _draft(id="CAD-100"))

    assert exc_info.value.status == 409
    assert _hash_tree(project.rootPath) == before


def test_create_rejects_a_slash_id_at_the_draft_boundary() -> None:
    # A traversal id never reaches create: TicketDraft.id is validated against
    # TICKET_ID_PATTERN at construction, so an id with a path separator is refused
    # at the DTO boundary before the writer is ever called. (The writer's own
    # PathTraversal defense is reachable on the edit/delete paths, whose id is a
    # plain unvalidated str — see the edit/delete unsafe-id tests below.)
    with pytest.raises(ValidationError):
        _draft(id="../escape")


# --------------------------------------------------------------------------- #
# edit — mutability gate + merge
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ticket_id", _TODO_IDS)
def test_edit_on_todo_ticket_mutates_and_rereads(tmp_path: Path, ticket_id: str) -> None:
    writer, project = _load(tmp_path)

    result = writer.edit_ticket(
        project, ticket_id, _edit(title="Retitled", bodyMarkdown="# Retitled\n\nNew body.\n")
    )

    assert result.applied is True
    assert result.ticket is not None
    assert result.ticket.title == "Retitled"
    assert result.ticket.bodyMarkdown == "# Retitled\n\nNew body.\n"
    # The real read adapter sees the mutation on disk.
    reread = RealFileAdapter().get_ticket(project, ticket_id)
    assert reread is not None
    assert reread.title == "Retitled"
    assert reread.bodyMarkdown == "# Retitled\n\nNew body.\n"


def test_edit_merges_unknown_manifest_fields(tmp_path: Path) -> None:
    # CAD-131's manifest entry carries a ``status`` the edit does not name; the merge
    # must preserve it (forward-compat), while updating the editable fields.
    writer, project = _load(tmp_path)
    writer.edit_ticket(project, "CAD-131", _edit(title="Kept status"))
    reread = RealFileAdapter().get_ticket(project, "CAD-131")
    assert reread is not None
    assert reread.title == "Kept status"
    assert reread.raw["status"] == "todo"  # untouched manifest field survives


def test_edit_id_absent_from_manifest_and_run_state_raises_ticket_not_mutable(
    tmp_path: Path,
) -> None:
    # T80: the gate runs FIRST (per the module docstring) and a project WITH a
    # run-state source resolves an id it has never heard of to RunState.absent,
    # not the mutable ``unknown`` — so the 409 gate masks the manifest's 404 here.
    # This is intentional per T80: "not mutable" is the honest answer when a
    # resolved source has nothing to say about the id.
    writer, project = _load(tmp_path)
    with pytest.raises(TicketNotMutable) as exc_info:
        writer.edit_ticket(project, "CAD-999", _edit())
    assert exc_info.value.status == 409
    assert exc_info.value.details == {"ticketId": "CAD-999", "runState": RunState.absent.value}


def test_edit_unknown_id_raises_unknown_ticket_with_no_run_state_source(tmp_path: Path) -> None:
    # With NO run-state source at all, an unheard-of id resolves the mutable
    # ``unknown`` (T80 does not touch this), the gate passes it through, and the
    # manifest lookup downstream is what reports the id does not exist.
    writer, project = _load_without_run_state(tmp_path)
    with pytest.raises(UnknownTicket) as exc_info:
        writer.edit_ticket(project, "CAD-999", _edit())
    assert exc_info.value.status == 404


def test_edit_unsafe_id_raises_path_traversal(tmp_path: Path) -> None:
    writer, project = _load(tmp_path)
    with pytest.raises(PathTraversal):
        writer.edit_ticket(project, "../escape", _edit())


@pytest.mark.parametrize(("ticket_id", "state"), list(_NON_MUTABLE.items()))
def test_edit_on_non_mutable_state_raises_and_leaves_bytes_identical(
    tmp_path: Path, ticket_id: str, state: RunState
) -> None:
    writer, project = _load(tmp_path)
    before = _hash_tree(project.rootPath)

    with pytest.raises(TicketNotMutable) as exc_info:
        writer.edit_ticket(project, ticket_id, _edit(title="Should not be written"))

    assert exc_info.value.status == 409
    assert exc_info.value.details == {"ticketId": ticket_id, "runState": state.value}
    # The core safety invariant: a refused edit writes nothing at all.
    assert _hash_tree(project.rootPath) == before


# --------------------------------------------------------------------------- #
# delete
# --------------------------------------------------------------------------- #


def test_delete_todo_ticket_removes_files_and_returns_snapshot(tmp_path: Path) -> None:
    writer, project = _load(tmp_path)

    result = writer.delete_ticket(project, "CAD-131")

    assert result.applied is True
    assert result.ticket is not None
    # The returned Ticket is the deleted ticket's final pre-delete snapshot.
    assert result.ticket.id == "CAD-131"
    assert result.ticket.title == "Weekly digest email"
    # It is gone from disk: the .md is unlinked and the manifest no longer lists it.
    assert not (project.ticketsDir / "CAD-131.md").exists()
    adapter = RealFileAdapter()
    assert "CAD-131" not in {s.id for s in adapter.list_tickets(project)}
    assert adapter.get_ticket(project, "CAD-131") is None
    # The roadmap line was removed.
    assert "CAD-131" not in project.roadmapPath.read_text()


def test_delete_id_absent_from_manifest_and_run_state_raises_ticket_not_mutable(
    tmp_path: Path,
) -> None:
    # See test_edit_id_absent_from_manifest_and_run_state_raises_ticket_not_mutable.
    writer, project = _load(tmp_path)
    with pytest.raises(TicketNotMutable) as exc_info:
        writer.delete_ticket(project, "CAD-999")
    assert exc_info.value.status == 409


def test_delete_unknown_id_raises_unknown_ticket_with_no_run_state_source(tmp_path: Path) -> None:
    writer, project = _load_without_run_state(tmp_path)
    with pytest.raises(UnknownTicket):
        writer.delete_ticket(project, "CAD-999")


def test_delete_unsafe_id_raises_path_traversal(tmp_path: Path) -> None:
    writer, project = _load(tmp_path)
    with pytest.raises(PathTraversal):
        writer.delete_ticket(project, "../escape")


@pytest.mark.parametrize(("ticket_id", "state"), list(_NON_MUTABLE.items()))
def test_delete_on_non_mutable_state_raises_and_leaves_bytes_identical(
    tmp_path: Path, ticket_id: str, state: RunState
) -> None:
    writer, project = _load(tmp_path)
    before = _hash_tree(project.rootPath)

    with pytest.raises(TicketNotMutable):
        writer.delete_ticket(project, ticket_id)

    # The refused delete removed nothing and touched nothing.
    assert (project.ticketsDir / f"{ticket_id}.md").is_file()
    assert _hash_tree(project.rootPath) == before


# --------------------------------------------------------------------------- #
# preview — pure unified diffs, no mutation on disk
# --------------------------------------------------------------------------- #


def test_preview_create_returns_diff_without_mutating_disk(tmp_path: Path) -> None:
    writer, project = _load(tmp_path)
    before = _hash_tree(project.rootPath)

    preview = writer.preview_create(project, _draft())

    assert isinstance(preview, DiffPreview)
    assert preview.ticketId == "CAD-200"
    assert _hash_tree(project.rootPath) == before


def test_preview_edit_returns_unified_diff_without_mutating_disk(tmp_path: Path) -> None:
    writer, project = _load(tmp_path)
    before = _hash_tree(project.rootPath)

    preview = writer.preview_edit(project, "CAD-131", _edit(title="Weekly digest email (revised)"))

    assert isinstance(preview, DiffPreview)
    manifest_diff = next(f for f in preview.files if f.path == _MANIFEST_REL)
    assert "@@" in manifest_diff.diff
    assert "--- a/" in manifest_diff.diff and "+++ b/" in manifest_diff.diff
    assert _hash_tree(project.rootPath) == before


def test_preview_edit_on_non_mutable_ticket_is_allowed_and_pure(tmp_path: Path) -> None:
    # Previews carry NO gate: a non-todo ticket can still be previewed (the UI's
    # disabled confirm is the UX guard) and it mutates nothing.
    writer, project = _load(tmp_path)
    before = _hash_tree(project.rootPath)
    preview = writer.preview_edit(project, "CAD-125", _edit(title="in-flight preview"))
    assert isinstance(preview, DiffPreview)
    assert _hash_tree(project.rootPath) == before


def test_preview_delete_returns_diff_without_mutating_disk(tmp_path: Path) -> None:
    writer, project = _load(tmp_path)
    before = _hash_tree(project.rootPath)

    preview = writer.preview_delete(project, "CAD-131")

    assert isinstance(preview, DiffPreview)
    md_diff = next(f for f in preview.files if f.path.endswith("CAD-131.md"))
    assert md_diff.changeKind == "delete"
    assert _hash_tree(project.rootPath) == before
