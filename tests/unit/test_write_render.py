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
from factory_console.file_adapter.manifest import MalformedManifest
from factory_console.file_adapter.path_safety import PathTraversal
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


def test_create_rechecks_duplicate_id_against_a_concurrent_manifest_write(
    tmp_path: Path, monkeypatch
) -> None:
    # Same race as render_edit/render_delete's: the first (load_manifest) read is
    # stale and reports no collision, but the file a live App Factory has since
    # rewritten (read raw by the second read) already carries this id. Appending
    # anyway would write a manifest with two entries sharing one id.
    project = _make_project(tmp_path)
    _seed(project)

    import factory_console.file_adapter.write_render as write_render_module

    real_load_manifest = write_render_module.load_manifest
    schema_version, entries = real_load_manifest(project.ticketsManifestPath)
    stale_entries = [entry for entry in entries if entry["id"] != "TM-050"]
    monkeypatch.setattr(
        write_render_module, "load_manifest", lambda path: (schema_version, stale_entries)
    )
    manifest = json.loads(json.dumps(_MANIFEST))  # deep copy
    manifest["tickets"].append({"id": "TM-050", "title": "Inserted by the factory"})
    _seed(project, manifest=manifest)

    with pytest.raises(TicketAlreadyExists) as exc_info:
        render_create(project, _draft(id="TM-050"))

    assert exc_info.value.details == {"ticketId": "TM-050"}


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


def test_edit_omitting_an_optional_field_leaves_the_manifest_value_alone(tmp_path: Path) -> None:
    # TicketEdit.track/milestone/dependsOn/files all have defaults, so a client that
    # never sends them (e.g. a bare curl PUT with just title+bodyMarkdown) must NOT
    # null them in the manifest — the .md header already guards this the same way
    # (see test_edit_omitting_track_and_milestone_keeps_the_headers_values).
    project = _make_project(tmp_path)
    _seed(project)

    sparse_edit = TicketEdit(title="Public trail-status REST endpoint (v2)", bodyMarkdown="# x\n")

    changes = render_edit(project, "TM-015", sparse_edit)

    entry = next(
        t
        for t in json.loads(_by_rel(changes, _MANIFEST_REL).newText)["tickets"]
        if t["id"] == "TM-015"
    )
    assert entry["title"] == "Public trail-status REST endpoint (v2)"  # supplied, changed
    assert entry["track"] == "api"  # not supplied, untouched
    assert entry["milestone"] == "v1"  # not supplied, untouched
    assert entry["dependsOn"] == ["TM-001"]  # not supplied, untouched
    assert entry["provides"] == "GET /api/v1/trails/{slug}/status"  # not supplied, untouched
    assert entry["files"] == ["server/trailmark/api/trail_status.py"]  # not supplied, untouched


def test_edit_preserves_a_multi_entry_provides_list_when_untouched(tmp_path: Path) -> None:
    # The SPA's edit form seeds its (scalar) provides field by joining an existing
    # list with ", " — an edit that never touches provides re-sends that same
    # joined string, and must not collapse the on-disk list to one fused entry.
    project = _make_project(tmp_path)
    manifest = json.loads(json.dumps(_MANIFEST))  # deep copy
    manifest["tickets"][1]["provides"] = ["GET /api/v1/trails/{slug}/status", "Trail SDK"]
    _seed(project, manifest=manifest)

    changes = render_edit(
        project, "TM-015", _edit(provides="GET /api/v1/trails/{slug}/status, Trail SDK")
    )

    entry = next(
        t
        for t in json.loads(_by_rel(changes, _MANIFEST_REL).newText)["tickets"]
        if t["id"] == "TM-015"
    )
    assert entry["provides"] == ["GET /api/v1/trails/{slug}/status", "Trail SDK"]


def test_edit_still_overwrites_provides_when_the_edit_actually_changes_it(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    manifest = json.loads(json.dumps(_MANIFEST))  # deep copy
    manifest["tickets"][1]["provides"] = ["GET /api/v1/trails/{slug}/status", "Trail SDK"]
    _seed(project, manifest=manifest)

    changes = render_edit(project, "TM-015", _edit(provides="A brand new capability"))

    entry = next(
        t
        for t in json.loads(_by_rel(changes, _MANIFEST_REL).newText)["tickets"]
        if t["id"] == "TM-015"
    )
    assert entry["provides"] == "A brand new capability"


def test_edit_md_renders_against_current_on_disk_text(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)
    md_path = project.ticketsDir / "TM-015.md"
    md_path.write_text("# Old body\n", encoding="utf-8")

    changes = render_edit(project, "TM-015", _edit())

    md = _by_rel(changes, "docs/planning/tickets/TM-015.md")
    assert md.currentText == "# Old body\n"
    assert md.newText == "# Endpoint\n\nUpdated body.\n"


def test_edit_preserves_existing_front_matter_when_edit_sends_none(tmp_path: Path) -> None:
    """An edit with no ``frontMatter`` must KEEP the ``.md``'s YAML header.

    The regression guard for the destructive default: ``TicketEdit.frontMatter``
    defaults to ``{}``, and no client can populate it (the read model and the SPA
    form both lack the field), so rendering from the edit alone silently deleted
    every factory-owned key on the very first ordinary edit.
    """
    project = _make_project(tmp_path)
    _seed(project)
    md_path = project.ticketsDir / "TM-015.md"
    md_path.write_text(
        "---\nid: TM-015\nstatus: todo\nowner: ranger-team\n---\n# Old body\n",
        encoding="utf-8",
    )

    changes = render_edit(project, "TM-015", _edit())

    md = _by_rel(changes, "docs/planning/tickets/TM-015.md")
    assert md.newText.startswith("---\n")
    assert "id: TM-015" in md.newText
    assert "status: todo" in md.newText
    assert "owner: ranger-team" in md.newText
    assert md.newText.endswith("# Endpoint\n\nUpdated body.\n")


def test_edit_front_matter_overrides_disk_and_adds_new_keys(tmp_path: Path) -> None:
    """A supplied ``frontMatter`` overlays the on-disk header rather than replacing it."""
    project = _make_project(tmp_path)
    _seed(project)
    md_path = project.ticketsDir / "TM-015.md"
    md_path.write_text("---\nid: TM-015\nowner: ranger-team\n---\n# Old body\n", encoding="utf-8")

    changes = render_edit(
        project, "TM-015", _edit(frontMatter={"owner": "api-team", "priority": "high"})
    )

    md = _by_rel(changes, "docs/planning/tickets/TM-015.md")
    assert "id: TM-015" in md.newText  # untouched key survives
    assert "owner: api-team" in md.newText  # supplied key wins
    assert "ranger-team" not in md.newText
    assert "priority: high" in md.newText  # new key added


def test_edit_without_an_md_file_renders_only_the_supplied_front_matter(tmp_path: Path) -> None:
    """A manifest entry whose ``.md`` is absent renders from the edit alone, not an error."""
    project = _make_project(tmp_path)
    _seed(project)  # no TM-015.md written

    changes = render_edit(project, "TM-015", _edit(frontMatter={"owner": "api-team"}))

    md = _by_rel(changes, "docs/planning/tickets/TM-015.md")
    assert md.currentText is None
    assert "owner: api-team" in md.newText
    assert md.newText.endswith("# Endpoint\n\nUpdated body.\n")


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


def test_edit_reindexes_against_a_concurrent_manifest_write(tmp_path, monkeypatch) -> None:
    # The console runs beside a live App Factory that can rewrite tickets.json
    # BETWEEN render_edit's two manifest reads. Simulate that by making the first
    # read (load_manifest) return a STALE entries list — as if an entry were
    # inserted ahead of TM-015 — while the real on-disk file (read by the second,
    # raw read) never changed. The fix re-derives the index from that second read,
    # so the right ticket is still edited instead of the wrong one (or a crash).
    project = _make_project(tmp_path)
    _seed(project)

    import factory_console.file_adapter.write_render as write_render_module

    real_load_manifest = write_render_module.load_manifest
    stale_entries = [
        {"id": "TM-999", "title": "just inserted by the factory"},
        *real_load_manifest(project.ticketsManifestPath)[1],
    ]
    monkeypatch.setattr(
        write_render_module,
        "load_manifest",
        lambda path: (real_load_manifest(path)[0], stale_entries),
    )

    changes = render_edit(project, "TM-015", _edit())

    tickets = json.loads(_by_rel(changes, _MANIFEST_REL).newText)["tickets"]
    edited = next(t for t in tickets if t["id"] == "TM-015")
    untouched = next(t for t in tickets if t["id"] == "TM-001")
    assert edited["title"] == "Public trail-status REST endpoint (v2)"
    assert untouched["title"] == "Ingest trail reports"


def test_edit_raises_malformed_manifest_when_a_concurrent_write_breaks_the_shape(
    tmp_path: Path, monkeypatch
) -> None:
    # A concurrent factory write can leave tickets.json valid JSON but structurally
    # broken (e.g. mid-rewrite). load_manifest (the first read) still reports the
    # old, valid entries, but the file the second (raw) read sees has since changed
    # shape — that read must raise the documented MalformedManifest, not an
    # unmapped TypeError/KeyError.
    project = _make_project(tmp_path)
    _seed(project)

    import factory_console.file_adapter.write_render as write_render_module

    real_load_manifest = write_render_module.load_manifest
    schema_version, entries = real_load_manifest(project.ticketsManifestPath)
    monkeypatch.setattr(
        write_render_module, "load_manifest", lambda path: (schema_version, entries)
    )
    project.ticketsManifestPath.write_text(json.dumps({"tickets": "not-a-list"}), encoding="utf-8")

    with pytest.raises(MalformedManifest):
        render_edit(project, "TM-015", _edit())


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


def test_delete_reindexes_against_a_concurrent_manifest_write(tmp_path, monkeypatch) -> None:
    # Same race as render_edit's: the first (load_manifest) read is stale, the
    # second (raw) read reflects the real on-disk file. The fix re-derives the
    # index from the second read, so delete removes the right ticket.
    project = _make_project(tmp_path)
    _seed(project)

    import factory_console.file_adapter.write_render as write_render_module

    real_load_manifest = write_render_module.load_manifest
    stale_entries = [
        {"id": "TM-999", "title": "just inserted by the factory"},
        *real_load_manifest(project.ticketsManifestPath)[1],
    ]
    monkeypatch.setattr(
        write_render_module,
        "load_manifest",
        lambda path: (real_load_manifest(path)[0], stale_entries),
    )

    changes = render_delete(project, "TM-015")

    tickets = json.loads(_by_rel(changes, _MANIFEST_REL).newText)["tickets"]
    assert {t["id"] for t in tickets} == {"TM-001"}


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


def test_edit_refreshes_front_matter_keys_it_also_writes_to_the_manifest(tmp_path: Path) -> None:
    """A header mirroring the manifest must track the edit, not keep stale values.

    ``_merge_edit`` overwrites ``title``/``track``/``milestone``/``dependsOn``/
    ``provides``/``files`` in ``tickets.json``, and real ticket headers duplicate
    exactly those keys. Preserving them verbatim left the ``.md`` contradicting the
    manifest entry rewritten in the SAME change-set — permanently, since every later
    edit re-based off the same stale copy.
    """
    project = _make_project(tmp_path)
    _seed(project)
    (project.ticketsDir / "TM-015.md").write_text(_FACTORY_SHAPED_MD, encoding="utf-8")

    changes = render_edit(project, "TM-015", _edit())

    md = _by_rel(changes, "docs/planning/tickets/TM-015.md")
    assert "title: Public trail-status REST endpoint (v2)" in md.newText
    assert "title: Public read API" not in md.newText
    assert "provides: GET /api/v1/trails/{slug}/status refreshed" in md.newText
    assert "the public board" not in md.newText  # the stale scalar is gone
    # Keys the edit does not own are still preserved.
    assert "id: TM-015" in md.newText
    assert "status: todo" in md.newText
    assert "owner: ranger-team" in md.newText

    # And the header now AGREES with the manifest entry rendered alongside it.
    entry = next(
        item
        for item in json.loads(_by_rel(changes, _MANIFEST_REL).newText)["tickets"]
        if item["id"] == "TM-015"
    )
    assert entry["title"] == "Public trail-status REST endpoint (v2)"
    assert f"title: {entry['title']}" in md.newText
    assert f"provides: {entry['provides']}" in md.newText


def test_edit_preserves_a_multi_entry_provides_list_in_the_header_when_untouched(
    tmp_path: Path,
) -> None:
    # The manifest-side guard (test_edit_preserves_a_multi_entry_provides_list_when_
    # untouched) has a header-side twin: a title-only edit must not fuse an existing
    # multi-entry ``provides`` list in the .md header into one scalar string either.
    project = _make_project(tmp_path)
    manifest = json.loads(json.dumps(_MANIFEST))  # deep copy
    manifest["tickets"][1]["provides"] = ["GET /api/v1/trails/{slug}/status", "Trail SDK"]
    _seed(project, manifest=manifest)
    (project.ticketsDir / "TM-015.md").write_text(
        "---\n"
        "id: TM-015\n"
        "title: Public read API\n"
        "status: todo\n"
        "provides:\n"
        "  - GET /api/v1/trails/{slug}/status\n"
        "  - Trail SDK\n"
        "---\n"
        "# Old body\n",
        encoding="utf-8",
    )

    changes = render_edit(
        project, "TM-015", _edit(provides="GET /api/v1/trails/{slug}/status, Trail SDK")
    )

    md = _by_rel(changes, "docs/planning/tickets/TM-015.md")
    assert "- GET /api/v1/trails/{slug}/status" in md.newText
    assert "- Trail SDK" in md.newText
    assert "GET /api/v1/trails/{slug}/status, Trail SDK" not in md.newText  # not fused


def test_edit_does_not_add_mirrored_keys_a_header_never_carried(tmp_path: Path) -> None:
    """Refreshing is limited to keys already on disk, so a sparse header stays sparse."""
    project = _make_project(tmp_path)
    _seed(project)
    (project.ticketsDir / "TM-015.md").write_text(
        "---\nid: TM-015\nowner: ranger-team\n---\n# Old body\n", encoding="utf-8"
    )

    changes = render_edit(project, "TM-015", _edit())

    md = _by_rel(changes, "docs/planning/tickets/TM-015.md")
    assert "title:" not in md.newText
    assert "dependsOn:" not in md.newText
    assert "owner: ranger-team" in md.newText


def test_edit_omitting_track_and_milestone_keeps_the_headers_values(tmp_path: Path) -> None:
    """A not-sent field must not null a real on-disk value.

    ``track`` and ``milestone`` are ``str | None = None`` and the SPA's edit form has
    no input for either, so on an ordinary edit they arrive as defaults rather than as
    intent. Refreshing them unconditionally did not merely let the header drift — it
    wrote ``track: null`` over the last correct copy, and every later edit then
    re-based off the nulled value, so the real one was unrecoverable.

    Constructed WITHOUT the ``_edit()`` helper on purpose: that helper supplies every
    field, which is exactly the case this bug hides behind.
    """
    project = _make_project(tmp_path)
    _seed(project)
    (project.ticketsDir / "TM-015.md").write_text(_FACTORY_SHAPED_MD, encoding="utf-8")

    changes = render_edit(
        project,
        "TM-015",
        TicketEdit(title="Renamed by the form", bodyMarkdown="# New body\n"),
    )

    md = _by_rel(changes, "docs/planning/tickets/TM-015.md")
    # The field the form DID send is refreshed...
    assert "title: Renamed by the form" in md.newText
    # ...and the ones it never sent keep their real values, not None.
    assert "track: api" in md.newText
    assert "milestone: v1" in md.newText
    assert "track: null" not in md.newText
    assert "milestone: null" not in md.newText
    assert "track:\n" not in md.newText


def test_edit_sending_track_null_still_clears_it(tmp_path: Path) -> None:
    """An EXPLICIT null is intent and must still be honored.

    The guard keys off ``model_fields_set``, not off the value, so "sent as null"
    (clear it) stays distinguishable from "not sent" (leave it) — otherwise the fix
    for the bug above would have made a field impossible to clear.
    """
    project = _make_project(tmp_path)
    _seed(project)
    (project.ticketsDir / "TM-015.md").write_text(_FACTORY_SHAPED_MD, encoding="utf-8")

    changes = render_edit(
        project,
        "TM-015",
        TicketEdit.model_validate({"title": "Kept", "track": None, "bodyMarkdown": "# New body\n"}),
    )

    md = _by_rel(changes, "docs/planning/tickets/TM-015.md")
    assert "track: api" not in md.newText
    # ...while a field that was still merely omitted is untouched.
    assert "milestone: v1" in md.newText


def test_body_only_edit_leaves_the_front_matter_block_byte_identical(tmp_path: Path) -> None:
    """Editing only the body must not reformat one character of the YAML header.

    The unified diff IS this feature's safety mechanism, so a round-trip through
    PyYAML that re-indents block sequences or folds an over-80-column scalar would
    fill the preview with churn on lines the user never touched.
    """
    project = _make_project(tmp_path)
    _seed(project)
    (project.ticketsDir / "TM-015.md").write_text(_FACTORY_SHAPED_MD, encoding="utf-8")

    # Every manifest-mirrored field left exactly as the header already has it.
    unchanged = _edit(
        title="Public read API",
        track="api",
        milestone="v1",
        dependsOn=["TM-001"],
        provides=("GET /api/v1/trails/{slug}/status for the signed-in ranger and the public board"),
        files=["server/trailmark/api/trail_status.py"],
        bodyMarkdown="# Brand new body\n",
    )
    changes = render_edit(project, "TM-015", unchanged)

    md = _by_rel(changes, "docs/planning/tickets/TM-015.md")
    header, _, _ = _FACTORY_SHAPED_MD.partition("# Old body")
    assert md.newText == header + "# Brand new body\n"
    assert "  - TM-001" in md.newText  # indentation preserved, not flattened
    assert "the public board\n" in md.newText  # long scalar not folded


def test_edit_refresh_keeps_the_on_disk_yaml_style(tmp_path: Path) -> None:
    """When the header IS re-dumped, it keeps the factory's indentation and width."""
    project = _make_project(tmp_path)
    _seed(project)
    (project.ticketsDir / "TM-015.md").write_text(_FACTORY_SHAPED_MD, encoding="utf-8")

    changes = render_edit(project, "TM-015", _edit(dependsOn=["TM-001", "TM-002"]))

    md = _by_rel(changes, "docs/planning/tickets/TM-015.md")
    assert "  - TM-001\n  - TM-002\n" in md.newText
    # A refreshed long scalar stays on ONE line rather than being folded at 80 cols.
    long_lines = [line for line in md.newText.splitlines() if line.startswith("provides: ")]
    assert long_lines == ["provides: GET /api/v1/trails/{slug}/status refreshed"]


def test_edit_ignores_factory_owned_keys_sent_in_front_matter(tmp_path: Path) -> None:
    """``frontMatter`` is an open dict on a public route; reserved keys must not pass.

    ``_merge_edit`` refuses to let a client set ``id``/``status`` in the manifest, so
    letting them through here would desynchronize the ``.md`` from the entry written
    beside it — with the todo-only gate unable to notice, since it authorizes off the
    run-state directory rather than the file.
    """
    project = _make_project(tmp_path)
    _seed(project)
    (project.ticketsDir / "TM-015.md").write_text(_FACTORY_SHAPED_MD, encoding="utf-8")

    changes = render_edit(
        project,
        "TM-015",
        _edit(frontMatter={"status": "merged", "id": "OTHER-1", "reviewer": "sam"}),
    )

    md = _by_rel(changes, "docs/planning/tickets/TM-015.md")
    assert "status: todo" in md.newText
    assert "status: merged" not in md.newText
    assert "id: TM-015" in md.newText
    assert "OTHER-1" not in md.newText
    assert "reviewer: sam" in md.newText  # a key the client legitimately owns


def test_create_ignores_factory_owned_keys_sent_in_front_matter(tmp_path: Path) -> None:
    """The create path filters ``frontMatter`` the same way the edit path does."""
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_create(
        project, _draft(frontMatter={"status": "merged", "title": "Spoofed", "reviewer": "sam"})
    )

    md = _by_rel(changes, "docs/planning/tickets/TM-050.md")
    assert "status: merged" not in md.newText
    assert "Spoofed" not in md.newText
    assert "reviewer: sam" in md.newText


def test_edit_refuses_a_ticket_md_that_exists_but_cannot_be_read(tmp_path: Path) -> None:
    """An unreadable ``.md`` must fail closed, not be rebuilt from the request alone.

    Treating "unreadable" like "absent" made the edit proceed and overwrite the file
    with a header rebuilt from the request — destroying the very front matter the
    merge exists to preserve, on a file the server could not even read.
    """
    project = _make_project(tmp_path)
    _seed(project)
    (project.ticketsDir / "TM-015.md").write_bytes(b"---\nid: TM-015\n---\n\xff\xfe not utf-8\n")

    with pytest.raises(TicketFileUnreadable) as excinfo:
        render_edit(project, "TM-015", _edit())

    assert excinfo.value.status == 500
    # The envelope names the ticket, never a filesystem path.
    assert "TM-015" in str(excinfo.value.details)
    assert str(tmp_path) not in to_error_response(excinfo.value)["error"]["message"]
