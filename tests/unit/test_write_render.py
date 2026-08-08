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
from factory_console.file_adapter.ticket_content import TicketFormatRetired
from factory_console.file_adapter.ticket_json import parse_ticket_content
from factory_console.file_adapter.ticket_md import TicketFileUnreadable
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
            "path": "docs/planning/tickets/TM-001.json",
        },
        {
            "id": "TM-015",
            "title": "Public trail-status REST endpoint",
            "status": "todo",
            "track": "api",
            "milestone": "v1",
            "dependsOn": ["TM-001"],
            "provides": "GET /api/v1/trails/{slug}/status",
            "path": "docs/planning/tickets/TM-015.json",
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
        "context": "Why this ticket exists.",
        "approach": "1. Build it.\n2. Verify it.",
        "criticalFiles": ["server/trailmark/mobile/capture.py"],
        "interfaceData": "N/A",
        "verificationCommands": ["pytest -q"],
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
        "context": "Why this ticket exists.",
        "approach": "1. Build it.\n2. Verify it.",
        "criticalFiles": ["server/trailmark/api/trail_status.py"],
        "interfaceData": "N/A",
        "verificationCommands": ["pytest -q"],
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
        "docs/planning/tickets/TM-050.json",
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


def test_create_writes_a_schema_valid_v3_content_file(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_create(project, _draft())

    content = _by_rel(changes, "docs/planning/tickets/TM-050.json")
    assert content.currentText is None  # a new file
    assert content.newText is not None
    # Parsed by the READER's own validation, so "the console wrote it" and "the console
    # (and therefore the factory, whose schema it mirrors) will accept it" are one claim.
    parsed = parse_ticket_content("TM-050", content.newText)
    assert parsed.critical_files == ["server/trailmark/mobile/capture.py"]
    assert parsed.verification.commands == ["pytest -q"]


def test_create_content_file_key_order_matches_the_factory_s(tmp_path: Path) -> None:
    # Two producers writing the same document with different key orders make every
    # factory-written ticket diff against every console-written one on the next edit, on
    # bytes nobody changed. The order is ``fac_ticket_md_to_json``'s and the schema's.
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_create(project, _draft())

    content = _by_rel(changes, "docs/planning/tickets/TM-050.json")
    assert list(json.loads(content.newText)) == [
        "id",
        "context",
        "approach",
        "critical_files",
        "interface_data",
        "verification",
    ]
    assert content.newText.endswith("}\n")  # trailing newline + 2-space indent, like jq


def test_absent_notes_are_omitted_rather_than_written_null(tmp_path: Path) -> None:
    # A key present-and-empty is a different document from a key absent: it would show
    # as an added line in the diff of every ticket that has no notes, and it claims the
    # planner answered a question they did not.
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_create(project, _draft())

    content = _by_rel(changes, "docs/planning/tickets/TM-050.json")
    assert "notes" not in json.loads(content.newText)["verification"]


def test_the_manifest_entry_declares_the_content_path(tmp_path: Path) -> None:
    # Rather than leaving it to the reader's flat ``<ticketsDir>/<id>.md`` fallback,
    # which would look for the ticket under the wrong suffix — and which the factory's
    # own reader does not have at all.
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_create(project, _draft())

    entry = json.loads(_by_rel(changes, _MANIFEST_REL).newText)["tickets"][-1]
    assert entry["path"] == "docs/planning/tickets/TM-050.json"
    assert "files" not in entry, "v3's index has no files key; critical_files is the answer"


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
        "docs/planning/tickets/TM-050.json",
    }


def test_create_with_no_roadmap_path_skips_roadmap(tmp_path: Path) -> None:
    project = _make_project(tmp_path, with_roadmap=False)
    _seed(project, roadmap=None)

    changes = render_create(project, _draft())

    assert {change.relPath for change in changes} == {
        _MANIFEST_REL,
        "docs/planning/tickets/TM-050.json",
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


def test_edit_relabels_roadmap_line_in_place(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_edit(project, "TM-015", _edit(title="Public trail-status REST endpoint (v2)"))

    roadmap = _by_rel(changes, _ROADMAP_REL)
    assert "**TM-015** — Public trail-status REST endpoint (v2)" in roadmap.newText
    assert "Public read API (TM-015)" not in roadmap.newText


def test_edit_moves_roadmap_line_when_milestone_changes(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    # TM-015 starts under the ## v1 section; move it to MVP.
    changes = render_edit(project, "TM-015", _edit(milestone="MVP", title="Public read API"))

    roadmap = _by_rel(changes, _ROADMAP_REL)
    # The old in-place line under v1 is gone and the item now sits under MVP.
    assert "Public read API (TM-015)" not in roadmap.newText
    mvp_index = roadmap.newText.index("## MVP")
    v1_index = roadmap.newText.index("## v1")
    moved_index = roadmap.newText.index("**TM-015**")
    assert mvp_index < moved_index < v1_index


def test_edit_relabels_in_place_when_milestone_section_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    # No ## v99 section exists, so the line is relabelled in place, never lost.
    changes = render_edit(
        project, "TM-015", _edit(milestone="v99-nonexistent", title="Renamed here")
    )

    roadmap = _by_rel(changes, _ROADMAP_REL)
    v1_index = roadmap.newText.index("## v1")
    item_index = roadmap.newText.index("**TM-015** — Renamed here")
    assert item_index > v1_index  # still under its original v1 section


def test_edit_moves_preamble_item_into_its_milestone_section(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    # TM-015's roadmap line sits in the preamble (before any ## heading); the edit
    # gives it milestone MVP, whose section exists, so it moves under MVP.
    preamble_roadmap = (
        "# TrailMark Roadmap\n"
        "\n"
        "- [ ] Public read API (TM-015)\n"
        "\n"
        "## MVP — make ranger reports usable\n"
        "\n"
        "- [ ] Ingest trail reports (TM-001)\n"
    )
    _seed(project, roadmap=preamble_roadmap)

    changes = render_edit(project, "TM-015", _edit(milestone="MVP", title="Public read API"))

    roadmap = _by_rel(changes, _ROADMAP_REL)
    assert "Public read API (TM-015)" not in roadmap.newText  # left the preamble
    mvp_index = roadmap.newText.index("## MVP")
    moved_index = roadmap.newText.index("**TM-015**")
    assert moved_index > mvp_index  # now under the MVP section


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
    (project.ticketsDir / "TM-015.json").write_text('{"id": "TM-015"}\n', encoding="utf-8")

    changes = render_delete(project, "TM-015")

    content = _by_rel(changes, "docs/planning/tickets/TM-015.json")
    assert content.newText is None
    assert content.currentText == '{"id": "TM-015"}\n'


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


# --------------------------------------------------------------------------- #
# .md front matter vs the manifest entry rendered beside it
# --------------------------------------------------------------------------- #

# A header shaped like the ones the App Factory actually writes: the manifest fields
# mirrored into the YAML, two-space-indented block sequences, and a ``provides``
# scalar past PyYAML's default 80-column fold width.
_FACTORY_SHAPED_MD = """---
id: TM-015
title: Public read API
status: todo
track: api
milestone: v1
dependsOn:
  - TM-001
provides: GET /api/v1/trails/{slug}/status for the signed-in ranger and the public board
files:
  - server/trailmark/api/trail_status.py
owner: ranger-team
---
# Old body
"""


def test_edit_refuses_a_content_file_that_exists_but_cannot_be_read(tmp_path: Path) -> None:
    """An unreadable content file must fail closed, not be overwritten sight unseen.

    The reason narrowed when the content file stopped being merged — there is no longer
    a header the read protects — but it did not go away. The diff's ``currentText`` is
    what a human approves the write against, and answering "no current text" for a file
    the server could not read presents a REPLACE as a CREATE.
    """
    project = _make_project(tmp_path)
    _seed(project)
    (project.ticketsDir / "TM-015.json").write_bytes(b"\xff\xfe not utf-8\n")

    with pytest.raises(TicketFileUnreadable) as excinfo:
        render_edit(project, "TM-015", _edit())

    assert excinfo.value.status == 500
    # The envelope names the ticket, never a filesystem path.
    assert "TM-015" in str(excinfo.value.details)


def test_edit_on_a_markdown_ticket_is_refused_with_the_migration_command(
    tmp_path: Path,
) -> None:
    """The console reads ``.md`` and no longer writes it, so an edit refuses.

    Converting instead was the tempting alternative — the request carries all five
    structured fields, so the console COULD write them as JSON and repoint the manifest.
    That silently changes a file's format under a user who asked to change its text, and
    drops whatever the Markdown carried that the five fields do not. The factory's own
    migrator refuses to guess for the same reason.

    409, not 422: the request is well-formed. What is not v3 yet is the repository.
    """
    project = _make_project(tmp_path)
    legacy = {
        **_MANIFEST,
        "tickets": [
            {**_MANIFEST["tickets"][1], "path": "docs/planning/tickets/TM-015.md"},
        ],
    }
    _seed(project, manifest=legacy)
    (project.ticketsDir / "TM-015.md").write_text("# body\n", encoding="utf-8")

    with pytest.raises(TicketFormatRetired) as excinfo:
        render_edit(project, "TM-015", _edit())

    assert excinfo.value.status == 409
    assert excinfo.value.details["remedy"] == "factory-ticket migrate --repo <project root>"
    # Nothing on disk changed: the refusal happens before any change is computed.
    assert (project.ticketsDir / "TM-015.md").read_text("utf-8") == "# body\n"


def test_delete_is_not_gated_on_the_content_format(tmp_path: Path) -> None:
    """A ``.md`` ticket must stay DELETABLE, unlike editable.

    An edit has to produce a document in a format this console can write; a delete
    removes the file whatever it holds. Refusing here would leave a Markdown ticket
    undeletable through the very UI that lists it — a lockout whose only remedy is
    hand-editing the manifest.
    """
    project = _make_project(tmp_path)
    legacy = {
        **_MANIFEST,
        "tickets": [
            {**_MANIFEST["tickets"][1], "path": "docs/planning/tickets/TM-015.md"},
        ],
    }
    _seed(project, manifest=legacy)
    (project.ticketsDir / "TM-015.md").write_text("# body\n", encoding="utf-8")

    changes = render_delete(project, "TM-015")

    assert _by_rel(changes, "docs/planning/tickets/TM-015.md").newText is None
