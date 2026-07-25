"""Unit tests for :mod:`factory_console.file_adapter.write_render`.

Cover the three coupled renders (create/edit/delete) against ``tmp_path``
fixtures: the manifest entry mutation (append/merge/remove, preserving unknown
fields and top-level keys), the ``.md`` render (front-matter present/absent,
create vs delete), the tolerant roadmap section matching / in-place relabel /
line removal, the error transport contract (codes/statuses, no path leak), and
the PURITY guarantee — no file on disk is ever written. All I/O is confined to
``tmp_path`` so the suite is deterministic and hermetic.
"""

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from factory_console.domain import Project
from factory_console.domain.write import TicketDraft, TicketEdit
from factory_console.errors import to_error_response
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.write_render import (
    PlannedChange,
    TicketAlreadyExists,
    UnknownTicket,
    render_create,
    render_delete,
    render_edit,
)

_UNICODE_ESCAPE_RE = re.compile(r"\\u[0-9a-fA-F]{4}")

_MANIFEST = {
    "project": "trailmark",
    "schemaVersion": 1,
    "tickets": [
        {
            "id": "TM-001",
            "title": "Ingest trail reports",
            "status": "todo",
            "track": "ingestion",
            "milestone": "MVP",
            "dependsOn": [],
            "provides": "Nightly importer",
            "files": ["server/trailmark/ingest/csv_dropbox.py"],
        },
        {
            "id": "TM-015",
            "title": "Public trail-status REST endpoint",
            "status": "todo",
            "track": "api",
            "milestone": "v1",
            "dependsOn": ["TM-001"],
            "provides": "GET /api/v1/trails/{slug}/status",
            "files": ["server/trailmark/api/trail_status.py"],
            "estimate": "M",
        },
    ],
}

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


def _make_project(tmp_path: Path, *, with_roadmap: bool = True) -> Project:
    """Build a Project rooted at ``tmp_path/project`` with a real tickets dir."""
    root = tmp_path / "project"
    tickets_dir = root / "docs" / "planning" / "tickets"
    tickets_dir.mkdir(parents=True, exist_ok=True)
    return Project(
        rootPath=root,
        ticketsManifestPath=root / "docs" / "planning" / "tickets.json",
        ticketsDir=tickets_dir,
        roadmapPath=(root / "ROADMAP.md") if with_roadmap else None,
        discoveredAt=datetime(2026, 7, 20, 12, 0, 0),
    )


def _seed(
    project: Project, *, manifest: dict | None = None, roadmap: str | None = _ROADMAP
) -> None:
    """Write the manifest (and optionally the roadmap) into the project on disk."""
    payload = _MANIFEST if manifest is None else manifest
    project.ticketsManifestPath.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if roadmap is not None and project.roadmapPath is not None:
        project.roadmapPath.write_text(roadmap, encoding="utf-8")


def _snapshot(project: Project) -> dict[str, bytes]:
    """Capture the raw bytes of every file under the project root, keyed by path."""
    return {
        str(path): path.read_bytes()
        for path in sorted(project.rootPath.rglob("*"))
        if path.is_file()
    }


def _by_rel(changes: list[PlannedChange], rel: str) -> PlannedChange:
    """Return the single PlannedChange whose ``relPath`` equals ``rel``."""
    matches = [change for change in changes if change.relPath == rel]
    assert len(matches) == 1, f"expected exactly one change for {rel!r}, got {matches}"
    return matches[0]


_MANIFEST_REL = "docs/planning/tickets.json"
_ROADMAP_REL = "ROADMAP.md"


def _draft(**overrides: object) -> TicketDraft:
    base: dict[str, object] = {
        "id": "TM-050",
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
        "title": "Public trail-status REST endpoint (v2)",
        "track": "api",
        "milestone": "v1",
        "dependsOn": ["TM-001"],
        "provides": "GET /api/v1/trails/{slug}/status refreshed",
        "files": ["server/trailmark/api/trail_status.py"],
        "bodyMarkdown": "# Endpoint\n\nUpdated body.\n",
    }
    base.update(overrides)
    return TicketEdit(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# render_create
# --------------------------------------------------------------------------- #


def test_create_yields_three_planned_changes(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_create(project, _draft())

    assert {change.relPath for change in changes} == {
        _MANIFEST_REL,
        "docs/planning/tickets/TM-050.md",
        _ROADMAP_REL,
    }


def test_create_appends_new_manifest_entry_preserving_top_level_keys(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_create(project, _draft())

    manifest = _by_rel(changes, _MANIFEST_REL)
    parsed = json.loads(manifest.newText)
    assert parsed["project"] == "trailmark"
    assert parsed["schemaVersion"] == 1
    new_entry = parsed["tickets"][-1]
    assert new_entry["id"] == "TM-050"
    assert new_entry["status"] == "todo"
    assert new_entry["provides"] == "On-trail capture app"  # scalar string, not a list
    assert manifest.newText.endswith("}\n")  # trailing newline, 2-space indent format
    assert '  "project"' in manifest.newText


def test_create_md_change_has_front_matter_and_body(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_create(
        project, _draft(frontMatter={"owner": "ranger-team", "priority": "high"})
    )

    md = _by_rel(changes, "docs/planning/tickets/TM-050.md")
    assert md.currentText is None  # a new file
    assert md.newText is not None
    assert md.newText.startswith("---\n")
    assert "owner: ranger-team" in md.newText
    assert md.newText.endswith("# Capture\n\nBody text.\n")


def test_create_md_without_front_matter_has_no_fence(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_create(project, _draft(frontMatter={}))

    md = _by_rel(changes, "docs/planning/tickets/TM-050.md")
    assert md.newText == "# Capture\n\nBody text.\n"
    assert "---" not in md.newText


def test_create_inserts_roadmap_line_under_matching_section(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_create(project, _draft(milestone="MVP", title="Ranger mobile capture"))

    roadmap = _by_rel(changes, _ROADMAP_REL)
    assert "- [ ] **TM-050** — Ranger mobile capture" in roadmap.newText
    # The new line lands inside the MVP section, before the v1 heading.
    mvp_index = roadmap.newText.index("## MVP")
    v1_index = roadmap.newText.index("## v1")
    new_line_index = roadmap.newText.index("**TM-050**")
    assert mvp_index < new_line_index < v1_index


def test_create_preserves_non_ascii_manifest_verbatim(tmp_path: Path) -> None:
    # The factory writes tickets.json as raw UTF-8; a rendered manifest must keep
    # non-ASCII characters verbatim rather than escaping them to \uXXXX, or every
    # untouched entry would diff spuriously (and get mangled on apply).
    project = _make_project(tmp_path)
    manifest = json.loads(json.dumps(_MANIFEST))  # deep copy
    manifest["tickets"][0]["title"] = "Ingest trail reports → store"
    manifest["tickets"][1]["provides"] = "GET /api/v1/trails/{slug}/status — live"
    _seed(project, manifest=manifest)

    changes = render_create(project, _draft())

    new_text = _by_rel(changes, _MANIFEST_REL).newText
    assert "→ store" in new_text
    assert "status — live" in new_text
    assert _UNICODE_ESCAPE_RE.search(new_text) is None  # no \uXXXX escapes


def test_create_md_front_matter_preserves_non_ascii_verbatim(tmp_path: Path) -> None:
    # The ticket .md front-matter is rendered as raw UTF-8 too (allow_unicode=True),
    # consistent with the manifest — non-ASCII must not be escaped to \uXXXX.
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_create(project, _draft(frontMatter={"owner": "José", "note": "café —"}))

    md_text = _by_rel(changes, "docs/planning/tickets/TM-050.md").newText
    assert "owner: José" in md_text
    assert "café —" in md_text
    assert _UNICODE_ESCAPE_RE.search(md_text) is None  # no \uXXXX escapes


def test_create_raises_on_duplicate_id(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    with pytest.raises(TicketAlreadyExists) as exc_info:
        render_create(project, _draft(id="TM-001"))

    assert exc_info.value.code == "ticket_already_exists"
    assert exc_info.value.status == 409
    assert exc_info.value.details == {"ticketId": "TM-001"}


def test_create_with_no_matching_roadmap_section_skips_roadmap(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_create(project, _draft(milestone="v99-nonexistent"))

    assert {change.relPath for change in changes} == {
        _MANIFEST_REL,
        "docs/planning/tickets/TM-050.md",
    }


def test_create_with_no_roadmap_path_skips_roadmap(tmp_path: Path) -> None:
    project = _make_project(tmp_path, with_roadmap=False)
    _seed(project, roadmap=None)

    changes = render_create(project, _draft())

    assert {change.relPath for change in changes} == {
        _MANIFEST_REL,
        "docs/planning/tickets/TM-050.md",
    }


# --------------------------------------------------------------------------- #
# render_edit
# --------------------------------------------------------------------------- #


def test_edit_merges_and_preserves_unknown_estimate_field(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_edit(project, "TM-015", _edit())

    manifest = _by_rel(changes, _MANIFEST_REL)
    assert "estimate" in manifest.newText  # the unknown field survives verbatim
    entry = next(t for t in json.loads(manifest.newText)["tickets"] if t["id"] == "TM-015")
    assert entry["estimate"] == "M"
    assert entry["title"] == "Public trail-status REST endpoint (v2)"  # edited field changed
    assert entry["status"] == "todo"  # untouched field survives


def test_edit_md_renders_against_current_on_disk_text(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)
    md_path = project.ticketsDir / "TM-015.md"
    md_path.write_text("# Old body\n", encoding="utf-8")

    changes = render_edit(project, "TM-015", _edit())

    md = _by_rel(changes, "docs/planning/tickets/TM-015.md")
    assert md.currentText == "# Old body\n"
    assert md.newText == "# Endpoint\n\nUpdated body.\n"


def test_edit_relabels_roadmap_line_in_place(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_edit(project, "TM-015", _edit(title="Public trail-status REST endpoint (v2)"))

    roadmap = _by_rel(changes, _ROADMAP_REL)
    assert "**TM-015** — Public trail-status REST endpoint (v2)" in roadmap.newText
    assert "Public read API (TM-015)" not in roadmap.newText


def test_edit_raises_unknown_ticket(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    with pytest.raises(UnknownTicket) as exc_info:
        render_edit(project, "TM-999", _edit())

    assert exc_info.value.code == "ticket_not_found"
    assert exc_info.value.status == 404
    assert to_error_response(exc_info.value)["error"]["details"] == {"ticketId": "TM-999"}


# --------------------------------------------------------------------------- #
# render_delete
# --------------------------------------------------------------------------- #


def test_delete_removes_manifest_entry(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_delete(project, "TM-015")

    manifest = _by_rel(changes, _MANIFEST_REL)
    ids = [t["id"] for t in json.loads(manifest.newText)["tickets"]]
    assert ids == ["TM-001"]


def test_delete_marks_md_for_removal(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)
    (project.ticketsDir / "TM-015.md").write_text("# body\n", encoding="utf-8")

    changes = render_delete(project, "TM-015")

    md = _by_rel(changes, "docs/planning/tickets/TM-015.md")
    assert md.newText is None
    assert md.currentText == "# body\n"


def test_delete_drops_roadmap_line(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_delete(project, "TM-015")

    roadmap = _by_rel(changes, _ROADMAP_REL)
    assert "TM-015" not in roadmap.newText
    assert "TM-001" in roadmap.newText  # the sibling line is untouched


def test_delete_raises_unknown_ticket(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    with pytest.raises(UnknownTicket) as exc_info:
        render_delete(project, "TM-999")

    assert exc_info.value.status == 404


# --------------------------------------------------------------------------- #
# Path safety
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_id", ["../evil", "a/b", "foo/../../bar"])
def test_escaping_id_raises_path_traversal(tmp_path: Path, bad_id: str) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    with pytest.raises(PathTraversal) as exc_info:
        render_edit(project, bad_id, _edit())

    assert exc_info.value.code == "invalid_ticket_id"
    assert exc_info.value.status == 400
    # details echoes only the id, never a resolved filesystem path.
    assert exc_info.value.details == {"ticketId": bad_id}


def test_delete_escaping_id_raises_path_traversal(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    with pytest.raises(PathTraversal):
        render_delete(project, "../evil")


# --------------------------------------------------------------------------- #
# Purity — no file is ever written
# --------------------------------------------------------------------------- #


def test_renders_never_write_to_disk(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)
    (project.ticketsDir / "TM-015.md").write_text("# body\n", encoding="utf-8")
    before = _snapshot(project)

    render_create(project, _draft())
    render_edit(project, "TM-015", _edit())
    render_delete(project, "TM-015")

    assert _snapshot(project) == before
