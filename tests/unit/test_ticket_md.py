"""Unit tests for reading a MARKDOWN ticket, end to end through the dispatcher.

Cover front-matter splitting (present / absent / empty / malformed / non-mapping),
the defense-in-depth path guards (pattern-invalid id, slash id, symlink escaping
root), the missing-file case, ``enrich_ticket``'s merge semantics, and the error
transport contract (codes/statuses + no filesystem-path leak in ``details``).
All I/O is confined to ``tmp_path`` so the suite is deterministic and hermetic.

The behaviours under test now live in three modules — ``ticket_md`` owns the
front-matter split, ``path_safety`` owns the guards, ``ticket_content`` owns the
dispatch and the enrich — and these tests deliberately keep entering through
``read_ticket_body``, the composed path a request actually takes. Testing each
module through its own front door would leave the composition itself uncovered,
which is where a refactor of this shape does its damage. ``test_ticket_json.py``
is the JSON half of the same contract.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from factory_console.domain import Project, Ticket
from factory_console.errors import to_error_response
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.ticket_content import enrich_ticket, read_ticket_body
from factory_console.file_adapter.ticket_md import TicketFileMissing, TicketFileUnreadable


def read_ticket_md(project: Project, ticket_id: str) -> tuple[dict[str, Any], str]:
    """``(front_matter, body)`` for a Markdown ticket, via the format dispatcher.

    The shape the old module-level ``read_ticket_md`` returned, preserved here so the
    assertions below still read as statements about Markdown tickets rather than about
    a tuple-vs-NamedTuple change. ``TicketBody.content`` is asserted separately — it is
    ``None`` for this format, which is what keeps a Markdown ticket's manifest-declared
    ``files`` from being erased by a format that has no such field, and what tells the
    edit surface there are no structured fields here to offer.
    """
    body = read_ticket_body(project, ticket_id)
    assert body.content is None, "a Markdown ticket carries no structured content"
    return body.front_matter, body.markdown


def _make_project(tmp_path: Path) -> Project:
    """Build a Project rooted at ``tmp_path/project`` with a real tickets dir."""
    root = tmp_path / "project"
    tickets_dir = root / "docs" / "planning" / "tickets"
    tickets_dir.mkdir(parents=True, exist_ok=True)
    return Project(
        rootPath=root,
        ticketsManifestPath=root / "docs" / "planning" / "tickets.json",
        ticketsDir=tickets_dir,
        discoveredAt=datetime(2026, 7, 20, 12, 0, 0),
    )


def _write_ticket(project: Project, ticket_id: str, content: str) -> Path:
    """Write ``content`` to ``<ticketsDir>/<ticket_id>.md`` and return the path."""
    path = project.ticketsDir / f"{ticket_id}.md"
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Front-matter splitting
# --------------------------------------------------------------------------- #


def test_front_matter_present_returns_dict_and_body_without_fences(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    content = "---\nid: T1\ntitle: Hello\nstatus: todo\n---\n\n# Body\n\ntext\n"
    _write_ticket(project, "T1", content)

    front_matter, body = read_ticket_md(project, "T1")

    assert front_matter == {"id": "T1", "title": "Hello", "status": "todo"}
    assert body == "\n# Body\n\ntext\n"
    assert "---" not in body  # the fences are excluded from the body


def test_front_matter_absent_returns_empty_dict_and_full_text(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    content = "# Just a body\n\nno front matter here\n"
    _write_ticket(project, "T2", content)

    front_matter, body = read_ticket_md(project, "T2")

    assert front_matter == {}
    assert body == content


def test_empty_front_matter_returns_empty_dict_and_body_after_fence(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    content = "---\n---\n\n# Body only\n"
    _write_ticket(project, "T3", content)

    front_matter, body = read_ticket_md(project, "T3")

    assert front_matter == {}
    assert body == "\n# Body only\n"


def test_malformed_yaml_falls_back_to_full_text_without_raising(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    content = '---\nfoo: "unterminated\n---\n\n# Body\n'
    _write_ticket(project, "T4", content)

    front_matter, body = read_ticket_md(project, "T4")

    assert front_matter == {}
    assert body == content


def test_non_mapping_front_matter_falls_back_to_full_text(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    content = "---\n- a\n- b\n- c\n---\n\n# Body\n"
    _write_ticket(project, "T5", content)

    front_matter, body = read_ticket_md(project, "T5")

    assert front_matter == {}
    assert body == content


# --------------------------------------------------------------------------- #
# Path-safety guards
# --------------------------------------------------------------------------- #


def test_dotdot_slash_id_raises_path_traversal(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    with pytest.raises(PathTraversal):
        read_ticket_md(project, "../etc/passwd")


def test_slash_id_raises_path_traversal(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    with pytest.raises(PathTraversal):
        read_ticket_md(project, "a/b")


def test_missing_file_raises_ticket_file_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    with pytest.raises(TicketFileMissing):
        read_ticket_md(project, "T404")


def test_non_utf8_ticket_file_raises_ticket_file_unreadable(tmp_path: Path) -> None:
    # A ticket .md whose bytes are not valid UTF-8 must surface as the mapped
    # unreadable envelope, not escape as a raw UnicodeDecodeError / unmapped 500.
    project = _make_project(tmp_path)
    (project.ticketsDir / "Tbad.md").write_bytes(b"\xff\xfe not valid utf-8")
    with pytest.raises(TicketFileUnreadable):
        read_ticket_md(project, "Tbad")


def test_directory_at_ticket_path_raises_ticket_file_unreadable(tmp_path: Path) -> None:
    # A directory sitting at <id>.md is present-but-unreadable (IsADirectoryError,
    # an OSError) — it maps to the unreadable envelope, not a 404 or a raw 500.
    project = _make_project(tmp_path)
    (project.ticketsDir / "Tdir.md").mkdir()
    with pytest.raises(TicketFileUnreadable):
        read_ticket_md(project, "Tdir")


def test_symlink_escaping_root_raises_path_traversal(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    outside_file = tmp_path / "outside" / "secret.md"
    outside_file.parent.mkdir(parents=True)
    outside_file.write_text("secret\n", encoding="utf-8")

    link = project.ticketsDir / "evil.md"
    try:
        link.symlink_to(outside_file)
    except (OSError, NotImplementedError):
        pytest.skip("platform does not support symlinks")

    with pytest.raises(PathTraversal):
        read_ticket_md(project, "evil")


# --------------------------------------------------------------------------- #
# enrich_ticket
# --------------------------------------------------------------------------- #


def _make_stub(file_path: Path | None = None) -> Ticket:
    """A manifest stub: empty body/html, ``raw`` holding the manifest fields.

    ``file_path`` is where the manifest says this ticket's ``.md`` lives. It used
    to be an ignored placeholder because ``enrich_ticket`` re-derived the location
    from the id; it is now the authority, which is what lets a real repository put
    tickets under a milestone directory with a slug in the filename.
    """
    return Ticket(
        id="T7",
        title="Stub title",
        status="todo",
        track="file-adapter",
        milestone="MVP",
        dependsOn=["T6"],
        provides=["parser"],
        files=["server/factory_console/file_adapter/ticket_md.py"],
        filePath=file_path if file_path is not None else Path("unresolved-placeholder"),
        bodyMarkdown="",
        bodyHtml="",
        raw={"id": "T7", "title": "Stub title", "status": "todo"},
    )


def test_enrich_ticket_merges_body_front_matter_and_keeps_manifest_fields(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    stub = _make_stub(project.ticketsDir / "T7.md")
    _write_ticket(
        project,
        "T7",
        "---\ntitle: Front-matter title\nextra: value\n---\n\n# Real body\n",
    )

    enriched = enrich_ticket(project, stub)

    # Body replaced with the markdown after the fence; html left for T14.
    assert enriched.bodyMarkdown == "\n# Real body\n"
    assert enriched.bodyHtml == ""
    # filePath is the safely resolved on-disk path.
    assert enriched.filePath == (project.ticketsDir / "T7.md").resolve()
    # Front-matter is namespaced under raw['frontMatter'].
    assert enriched.raw["frontMatter"] == {"title": "Front-matter title", "extra": "value"}
    # Top-level manifest fields are untouched — manifest wins over front-matter.
    assert enriched.id == "T7"
    assert enriched.title == "Stub title"
    assert enriched.status == "todo"
    assert enriched.track == "file-adapter"
    assert enriched.milestone == "MVP"
    # A distinct, still-frozen Ticket instance.
    assert enriched is not stub
    with pytest.raises(ValidationError):
        enriched.title = "mutated"


# --------------------------------------------------------------------------- #
# Error transport contract
# --------------------------------------------------------------------------- #


def test_path_traversal_error_contract() -> None:
    exc = PathTraversal("../etc/passwd")
    assert exc.code == "invalid_ticket_id"
    assert exc.status == 400


def test_ticket_file_missing_error_contract() -> None:
    exc = TicketFileMissing("T404")
    assert exc.code == "ticket_file_missing"
    assert exc.status == 404


def test_ticket_file_unreadable_error_contract() -> None:
    exc = TicketFileUnreadable("T500")
    assert exc.code == "ticket_file_unreadable"
    assert exc.status == 500
    assert exc.details == {"ticketId": "T500"}


def test_error_details_never_leak_resolved_path(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    resolved_path = str((project.ticketsDir / "T1.md").resolve())

    for exc in (PathTraversal("T1"), TicketFileMissing("T1"), TicketFileUnreadable("T1")):
        envelope = to_error_response(exc)
        # Only the (user-supplied) ticket id is echoed back.
        assert envelope["error"]["details"] == {"ticketId": "T1"}
        serialized = json.dumps(envelope)
        assert resolved_path not in serialized
        assert str(project.rootPath) not in serialized
        assert str(project.ticketsDir) not in serialized
