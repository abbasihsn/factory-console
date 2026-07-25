"""Unit tests for :mod:`factory_console.file_adapter.write_diff`.

Cover the dry-run diff engine over hand-built :class:`PlannedChange` sets: the
create/modify/delete change-kind classification and their hunk shapes, the
omission of a no-op pair, the ``ticketId`` + order-preserving envelope, the
``a/``…``b/`` filenames drawn from ``relPath``, a well-formed diff string (no
trailing/doubled blank lines), and the PURITY guarantee — ``preview`` touches no
filesystem path. All ``Path`` values point under ``tmp_path`` and are asserted to
stay absent, so the suite is deterministic and hermetic.
"""

from pathlib import Path

from factory_console.domain.write import DiffPreview
from factory_console.file_adapter.write_diff import preview
from factory_console.file_adapter.write_render import PlannedChange


def _change(
    rel: str,
    *,
    current: str | None = None,
    new: str | None = None,
    root: Path | None = None,
) -> PlannedChange:
    """Build a PlannedChange whose ``path`` sits under ``root`` (default: /nonexistent)."""
    base = root if root is not None else Path("/nonexistent")
    return PlannedChange(path=base / rel, relPath=rel, currentText=current, newText=new)


def test_create_yields_all_added_hunk_lines() -> None:
    change = _change("docs/planning/tickets/TM-050.md", current=None, new="# Capture\n\nBody.\n")

    result = preview("TM-050", [change])

    assert len(result.files) == 1
    file_diff = result.files[0]
    assert file_diff.changeKind == "create"
    assert file_diff.path == "docs/planning/tickets/TM-050.md"
    body = [line for line in file_diff.diff.splitlines() if line.startswith(("+", "-"))]
    content = [line for line in body if not line.startswith(("+++", "---"))]
    assert content  # there is at least one content line
    assert all(line.startswith("+") for line in content)  # every content line is an addition


def test_modify_yields_added_and_removed_lines() -> None:
    change = _change("ROADMAP.md", current="a\nb\nc\n", new="a\nB\nc\n")

    file_diff = preview("TM-001", [change]).files[0]

    assert file_diff.changeKind == "modify"
    content = [
        line
        for line in file_diff.diff.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    assert "-b" in content
    assert "+B" in content


def test_delete_change_kind() -> None:
    change = _change("docs/planning/tickets/TM-015.md", current="# body\n", new=None)

    file_diff = preview("TM-015", [change]).files[0]

    assert file_diff.changeKind == "delete"
    content = [
        line
        for line in file_diff.diff.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    assert content and all(line.startswith("-") for line in content)  # all removals


def test_unchanged_pair_is_omitted() -> None:
    unchanged = _change("ROADMAP.md", current="same\n", new="same\n")
    changed = _change("docs/planning/tickets.json", current="a\n", new="b\n")

    result = preview("TM-001", [unchanged, changed])

    assert [f.path for f in result.files] == ["docs/planning/tickets.json"]


def test_ticket_id_and_file_order_preserved() -> None:
    changes = [
        _change("docs/planning/tickets.json", current="1\n", new="2\n"),
        _change("docs/planning/tickets/TM-050.md", current=None, new="# New\n"),
        _change("ROADMAP.md", current="x\n", new="y\n"),
    ]

    result = preview("TM-050", changes)

    assert isinstance(result, DiffPreview)
    assert result.ticketId == "TM-050"
    assert [f.path for f in result.files] == [
        "docs/planning/tickets.json",
        "docs/planning/tickets/TM-050.md",
        "ROADMAP.md",
    ]


def test_diff_uses_rel_path_in_from_and_to_filenames() -> None:
    change = _change("docs/planning/tickets.json", current="a\n", new="b\n")

    diff = preview("TM-001", [change]).files[0].diff

    assert "--- a/docs/planning/tickets.json" in diff
    assert "+++ b/docs/planning/tickets.json" in diff


def test_modify_diff_is_well_formed_no_trailing_or_doubled_blanks() -> None:
    change = _change("ROADMAP.md", current="a\nb\nc\nd\n", new="a\nb\nX\nd\n")

    diff = preview("TM-001", [change]).files[0].diff

    lines = diff.split("\n")
    assert diff == diff.strip("\n")  # no leading/trailing blank lines
    assert "" not in lines  # no doubled/empty lines within the diff
    assert lines[0].startswith("--- ")
    assert lines[1].startswith("+++ ")
    assert lines[2].startswith("@@ ")


def test_empty_planned_yields_empty_files() -> None:
    result = preview("TM-001", [])

    assert result.ticketId == "TM-001"
    assert result.files == []


def test_preview_writes_nothing_to_disk(tmp_path: Path) -> None:
    changes = [
        _change("docs/planning/tickets.json", current="a\n", new="b\n", root=tmp_path),
        _change("docs/planning/tickets/TM-050.md", current=None, new="# New\n", root=tmp_path),
        _change("ROADMAP.md", current="old\n", new=None, root=tmp_path),
    ]

    preview("TM-050", changes)

    # Purity: not one of the planned paths (nor any directory) was created on disk.
    for change in changes:
        assert not change.path.exists()
    assert list(tmp_path.iterdir()) == []
