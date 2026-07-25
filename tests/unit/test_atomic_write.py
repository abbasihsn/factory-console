"""Unit tests for :mod:`factory_console.file_adapter.atomic_write`.

Cover the console's single sanctioned write site against ``tmp_path`` fixtures:
the happy-path apply of the three coupled files (manifest / ``.md`` / roadmap) with
re-read verification and returned relPaths, a delete that unlinks the ``.md``, the
containment + run-state hard guards refusing BEFORE any write, a mid-apply
``os.replace`` failure surfacing as :class:`AtomicWriteError` with no dangling temp
files, parent-dir creation, and a real :class:`RealFileAdapter` round-trip after a
render + apply. All I/O is confined to ``tmp_path`` so the suite is hermetic.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from factory_console.domain import Project
from factory_console.domain.write import TicketDraft
from factory_console.file_adapter import atomic_write
from factory_console.file_adapter.atomic_write import AtomicWriteError, apply_changes
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.real import RealFileAdapter
from factory_console.file_adapter.run_state import RUN_STATE_RELATIVE_LOCATIONS
from factory_console.file_adapter.write_render import PlannedChange, render_create

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
    ],
}

_ROADMAP = (
    "# TrailMark Roadmap\n"
    "\n"
    "## MVP — make ranger reports usable\n"
    "\n"
    "- [ ] Ingest trail reports (TM-001)\n"
)

_MANIFEST_REL = "docs/planning/tickets.json"
_ROADMAP_REL = "ROADMAP.md"


def _make_project(tmp_path: Path, *, with_run_state_dir: bool = False) -> Project:
    """Build a Project rooted at ``tmp_path/project`` with a real tickets dir."""
    root = tmp_path / "project"
    tickets_dir = root / "docs" / "planning" / "tickets"
    tickets_dir.mkdir(parents=True, exist_ok=True)
    run_state_dir: Path | None = None
    if with_run_state_dir:
        run_state_dir = root / ".factory" / "run-state"
        run_state_dir.mkdir(parents=True, exist_ok=True)
    return Project(
        rootPath=root,
        ticketsManifestPath=root / "docs" / "planning" / "tickets.json",
        ticketsDir=tickets_dir,
        roadmapPath=root / "ROADMAP.md",
        runStateDir=run_state_dir,
        discoveredAt=datetime(2026, 7, 20, 12, 0, 0),
    )


def _seed(project: Project) -> None:
    """Write the manifest and roadmap into the project on disk."""
    project.ticketsManifestPath.write_text(json.dumps(_MANIFEST, indent=2) + "\n", encoding="utf-8")
    assert project.roadmapPath is not None
    project.roadmapPath.write_text(_ROADMAP, encoding="utf-8")


def _snapshot(project: Project) -> dict[str, bytes]:
    """Capture the raw bytes of every file under the project root, keyed by path."""
    return {
        str(path): path.read_bytes()
        for path in sorted(project.rootPath.rglob("*"))
        if path.is_file()
    }


def _planned(project: Project, rel: str, new_text: str | None) -> PlannedChange:
    """Build a PlannedChange targeting ``rel`` under the project root."""
    return PlannedChange(
        path=project.rootPath / rel,
        relPath=rel,
        currentText=None,
        newText=new_text,
    )


def _leftover_temps(directory: Path) -> list[Path]:
    """Return any leftover ``mkstemp`` ``.tmp`` files in ``directory``."""
    if not directory.is_dir():
        return []
    return [entry for entry in directory.iterdir() if entry.name.endswith(".tmp")]


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


# --------------------------------------------------------------------------- #
# Happy-path apply
# --------------------------------------------------------------------------- #


def test_apply_writes_all_three_files_and_returns_relpaths(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)
    md_rel = "docs/planning/tickets/TM-050.md"
    changes = [
        _planned(project, _MANIFEST_REL, '{"tickets": []}\n'),
        _planned(project, md_rel, "# TM-050\n\nBody.\n"),
        _planned(project, _ROADMAP_REL, "# Roadmap\n\n- [ ] **TM-050**\n"),
    ]

    written = apply_changes(project, changes)

    assert set(written) == {_MANIFEST_REL, md_rel, _ROADMAP_REL}
    for change in changes:
        assert change.newText is not None
        assert (project.rootPath / change.relPath).read_text(encoding="utf-8") == change.newText


def test_apply_returns_relpaths_in_manifest_md_roadmap_order(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)
    md_rel = "docs/planning/tickets/TM-050.md"
    # Deliberately supply the changes OUT of apply order; apply must reorder them.
    changes = [
        _planned(project, _ROADMAP_REL, "# Roadmap\n"),
        _planned(project, md_rel, "# md\n"),
        _planned(project, _MANIFEST_REL, "{}\n"),
    ]

    written = apply_changes(project, changes)

    assert written == [_MANIFEST_REL, md_rel, _ROADMAP_REL]


def test_apply_delete_unlinks_md(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)
    md_rel = "docs/planning/tickets/TM-050.md"
    md_path = project.rootPath / md_rel
    md_path.write_text("# to be removed\n", encoding="utf-8")

    written = apply_changes(project, [_planned(project, md_rel, None)])

    assert written == [md_rel]
    assert not md_path.exists()


def test_apply_delete_missing_md_is_a_noop(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)
    md_rel = "docs/planning/tickets/TM-999.md"

    written = apply_changes(project, [_planned(project, md_rel, None)])

    assert written == [md_rel]


def test_apply_creates_missing_parent_dir(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)
    nested_rel = "docs/planning/tickets/nested/TM-050.md"
    target = project.rootPath / nested_rel
    assert not target.parent.exists()

    apply_changes(project, [_planned(project, nested_rel, "# nested\n")])

    assert target.read_text(encoding="utf-8") == "# nested\n"


# --------------------------------------------------------------------------- #
# Run-state hard guard — refuse BEFORE any write
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "location",
    [location.as_posix() for location in RUN_STATE_RELATIVE_LOCATIONS],
)
def test_run_state_relative_location_refused_before_any_write(
    tmp_path: Path, location: str
) -> None:
    project = _make_project(tmp_path)
    _seed(project)
    before = _snapshot(project)
    run_state_rel = f"{location}/todo/TM-050"
    changes = [
        # A valid manifest change paired with the forbidden one: the guard must
        # refuse the whole set before the manifest is ever touched.
        _planned(project, _MANIFEST_REL, '{"tickets": []}\n'),
        _planned(project, run_state_rel, "marker"),
    ]

    with pytest.raises(PathTraversal) as exc_info:
        apply_changes(project, changes)

    assert exc_info.value.code == "invalid_ticket_id"
    assert exc_info.value.status == 400
    # PathTraversal echoes the passed identifier (the safe project-relative
    # relPath) under its ``ticketId`` detail key — never an absolute path.
    assert exc_info.value.details == {"ticketId": run_state_rel}
    assert not (project.rootPath / run_state_rel).exists()
    assert _snapshot(project) == before  # nothing written


def test_discovered_run_state_dir_refused_before_any_write(tmp_path: Path) -> None:
    project = _make_project(tmp_path, with_run_state_dir=True)
    _seed(project)
    assert project.runStateDir is not None
    before = _snapshot(project)
    run_state_rel = ".factory/run-state/ready/TM-050"
    changes = [
        _planned(project, _MANIFEST_REL, '{"tickets": []}\n'),
        _planned(project, run_state_rel, "marker"),
    ]

    with pytest.raises(PathTraversal):
        apply_changes(project, changes)

    assert not (project.rootPath / run_state_rel).exists()
    assert _snapshot(project) == before


# --------------------------------------------------------------------------- #
# Containment guard
# --------------------------------------------------------------------------- #


def test_path_escaping_root_raises_before_any_write(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)
    before = _snapshot(project)
    escaping = PlannedChange(
        path=project.rootPath / ".." / "outside.txt",
        relPath="../outside.txt",
        currentText=None,
        newText="escaped",
    )
    changes = [_planned(project, _MANIFEST_REL, '{"tickets": []}\n'), escaping]

    with pytest.raises(PathTraversal) as exc_info:
        apply_changes(project, changes)

    assert exc_info.value.status == 400
    assert not (tmp_path / "outside.txt").exists()
    assert _snapshot(project) == before


# --------------------------------------------------------------------------- #
# Mid-apply failure — atomic surfacing + no dangling temps
# --------------------------------------------------------------------------- #


def test_replace_failure_on_second_file_surfaces_atomic_write_error_no_temps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path)
    _seed(project)
    md_rel = "docs/planning/tickets/TM-050.md"
    changes = [
        _planned(project, _MANIFEST_REL, '{"tickets": []}\n'),
        _planned(project, md_rel, "# md\n"),
    ]

    real_replace = os.replace
    state = {"calls": 0}

    def flaky_replace(src: object, dst: object) -> None:
        state["calls"] += 1
        if state["calls"] == 2:
            raise OSError("simulated replace failure")
        real_replace(src, dst)

    monkeypatch.setattr(atomic_write.os, "replace", flaky_replace)

    with pytest.raises(AtomicWriteError) as exc_info:
        apply_changes(project, changes)

    assert exc_info.value.status == 500
    assert exc_info.value.details == {"relPath": md_rel}
    assert isinstance(exc_info.value.__cause__, OSError)  # chained from the I/O error
    # No dangling mkstemp temp files in either target directory.
    assert _leftover_temps(project.ticketsManifestPath.parent) == []
    assert _leftover_temps(project.ticketsDir) == []


# --------------------------------------------------------------------------- #
# Integration — real re-read round-trip after render + apply
# --------------------------------------------------------------------------- #


def test_real_adapter_rereads_project_after_apply(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed(project)

    changes = render_create(project, _draft())
    apply_changes(project, changes)

    adapter = RealFileAdapter()
    summaries = adapter.list_tickets(project)
    assert {summary.id for summary in summaries} == {"TM-001", "TM-050"}

    ticket = adapter.get_ticket(project, "TM-050")
    assert ticket is not None
    assert ticket.bodyMarkdown == "# Capture\n\nBody text.\n"

    roadmap = adapter.get_roadmap(project)
    assert roadmap is not None
    assert "**TM-050** — Ranger mobile capture" in roadmap.bodyMarkdown
