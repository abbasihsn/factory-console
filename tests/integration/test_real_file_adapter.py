"""Integration tests for the filesystem-backed :class:`RealFileAdapter`.

These exercise the real adapter end-to-end against the checked-in fixture
projects under ``tests/fixtures/projects/`` — composing real manifest parsing,
``.md`` reading, markdown rendering, and run-state probing — rather than the
in-memory fake. ``with_run_state`` is the happy-path project (6 tickets spanning
every run-state, a dangling dependency edge, and a root ``ROADMAP.md``);
``malformed`` pins that a bad manifest surfaces as :class:`MalformedManifest`.

Fixture paths are resolved from ``parents[1]`` (the ``tests/`` directory) so the
module runs identically under ``pytest`` from any cwd — matching
``tests/unit/test_fixtures_shape.py``.
"""

import shutil
from pathlib import Path

import pytest

from factory_console.domain import (
    DepNeighborhood,
    Project,
    Roadmap,
    RunState,
    Ticket,
    TicketSummary,
)
from factory_console.domain.search import SearchHit
from factory_console.file_adapter import FileAdapter
from factory_console.file_adapter.manifest import MalformedManifest
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.real import RealFileAdapter, RoadmapUnreadable

PROJECTS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "projects"
WITH_RUN_STATE = PROJECTS_DIR / "with_run_state"
MALFORMED = PROJECTS_DIR / "malformed"

MANIFEST_ORDER = ["CAD-100", "CAD-118", "CAD-125", "CAD-131", "CAD-140", "CAD-152"]


def _load_with_run_state() -> tuple[RealFileAdapter, Project]:
    """Return a fresh adapter and the loaded ``with_run_state`` project."""
    adapter = RealFileAdapter()
    return adapter, adapter.load_project(WITH_RUN_STATE)


def _summary_by_id(summaries: list[TicketSummary], ticket_id: str) -> TicketSummary:
    return next(summary for summary in summaries if summary.id == ticket_id)


# --------------------------------------------------------------------------- #
# runtime_checkable Protocol gate
# --------------------------------------------------------------------------- #


def test_real_adapter_satisfies_runtime_checkable_file_adapter() -> None:
    assert isinstance(RealFileAdapter(), FileAdapter)


# --------------------------------------------------------------------------- #
# load_project
# --------------------------------------------------------------------------- #


def test_load_project_resolves_every_project_path() -> None:
    _adapter, project = _load_with_run_state()
    # Paths are computed relative to the discovered (resolved) root; assert both
    # the by-construction relationship and that each target exists on disk.
    assert project.ticketsManifestPath == project.rootPath / "docs" / "planning" / "tickets.json"
    assert project.ticketsManifestPath.is_file()
    assert project.ticketsDir == project.rootPath / "docs" / "planning" / "tickets"
    assert project.ticketsDir.is_dir()
    assert project.roadmapPath == project.rootPath / "ROADMAP.md"
    assert project.roadmapPath is not None and project.roadmapPath.is_file()
    assert project.runStateDir == project.rootPath / ".factory" / "run-state"
    assert project.runStateDir is not None and project.runStateDir.is_dir()
    # discoveredAt must be timezone-aware.
    assert project.discoveredAt.tzinfo is not None


# --------------------------------------------------------------------------- #
# list_tickets — order, run-state, and edge counts
# --------------------------------------------------------------------------- #


def test_list_tickets_returns_six_summaries_in_manifest_order() -> None:
    adapter, project = _load_with_run_state()
    summaries = adapter.list_tickets(project)
    assert [summary.id for summary in summaries] == MANIFEST_ORDER
    assert all(isinstance(summary, TicketSummary) for summary in summaries)


def test_list_tickets_resolves_run_state_per_ticket() -> None:
    adapter, project = _load_with_run_state()
    run_states = {summary.id: summary.runState for summary in adapter.list_tickets(project)}
    assert run_states == {
        "CAD-100": RunState.merged,
        "CAD-118": RunState.ready,
        "CAD-125": RunState.in_flight,
        "CAD-131": RunState.todo,
        "CAD-140": RunState.todo,
        # Present run-state dir, no marker for CAD-152 -> todo (not unknown).
        "CAD-152": RunState.todo,
    }


def test_list_tickets_computes_dep_and_dependent_counts() -> None:
    adapter, project = _load_with_run_state()
    counts = {
        summary.id: (summary.depCount, summary.dependentCount)
        for summary in adapter.list_tickets(project)
    }
    # (depCount, dependentCount); depCount includes the dangling
    # CAD-207-nonexistent edge declared by CAD-131.
    assert counts == {
        "CAD-100": (0, 2),
        "CAD-118": (1, 1),
        "CAD-125": (2, 2),
        "CAD-131": (2, 0),
        "CAD-140": (1, 1),
        "CAD-152": (1, 0),
    }


# --------------------------------------------------------------------------- #
# get_ticket
# --------------------------------------------------------------------------- #


def test_get_ticket_returns_full_ticket_with_rendered_html() -> None:
    adapter, project = _load_with_run_state()
    ticket = adapter.get_ticket(project, "CAD-131")
    assert isinstance(ticket, Ticket)
    assert ticket.id == "CAD-131"
    # Body markdown is the on-disk body with the front-matter fence split off.
    assert ticket.bodyMarkdown.strip()
    assert "# Weekly digest email" in ticket.bodyMarkdown
    # bodyHtml is real rendered, sanitized markup — a heading and the GFM table.
    assert "<h1>" in ticket.bodyHtml
    assert "<table>" in ticket.bodyHtml
    # Parsed YAML front-matter is namespaced under raw['frontMatter'].
    assert ticket.raw["frontMatter"]
    assert ticket.raw["frontMatter"]["id"] == "CAD-131"


def test_get_ticket_returns_none_for_id_absent_from_manifest() -> None:
    adapter, project = _load_with_run_state()
    assert adapter.get_ticket(project, "CAD-999") is None


# --------------------------------------------------------------------------- #
# get_deps — neighborhood shape, order, unresolved, dependents
# --------------------------------------------------------------------------- #


def test_get_deps_resolves_direct_deps_and_dependents() -> None:
    adapter, project = _load_with_run_state()
    neighborhood = adapter.get_deps(project, "CAD-125")
    assert isinstance(neighborhood, DepNeighborhood)
    assert neighborhood.ticket.id == "CAD-125"
    assert [dep.id for dep in neighborhood.directDeps] == ["CAD-100", "CAD-118"]
    assert [dependent.id for dependent in neighborhood.directDependents] == ["CAD-131", "CAD-140"]
    assert neighborhood.unresolvedDeps == []


def test_get_deps_surfaces_unresolved_dep_edge() -> None:
    adapter, project = _load_with_run_state()
    neighborhood = adapter.get_deps(project, "CAD-131")
    assert neighborhood is not None
    assert [dep.id for dep in neighborhood.directDeps] == ["CAD-125"]
    assert neighborhood.unresolvedDeps == ["CAD-207-nonexistent"]
    assert neighborhood.directDependents == []


def test_get_deps_returns_none_for_id_absent_from_manifest() -> None:
    adapter, project = _load_with_run_state()
    assert adapter.get_deps(project, "CAD-999") is None


def test_list_and_deps_views_share_one_projection() -> None:
    # The list view and the dependency view must never disagree: the summary a
    # ticket gets in list_tickets is field-for-field the summary it gets as the
    # subject of its own get_deps neighborhood.
    adapter, project = _load_with_run_state()
    list_summary = _summary_by_id(adapter.list_tickets(project), "CAD-125")
    deps = adapter.get_deps(project, "CAD-125")
    assert deps is not None
    assert deps.ticket == list_summary


# --------------------------------------------------------------------------- #
# read_run_state
# --------------------------------------------------------------------------- #


def test_read_run_state_probes_markers_and_defaults_to_todo() -> None:
    adapter, project = _load_with_run_state()
    assert adapter.read_run_state(project, "CAD-125") is RunState.in_flight
    assert adapter.read_run_state(project, "CAD-100") is RunState.merged
    # Present run-state dir but no marker for CAD-152 -> todo, not unknown.
    assert adapter.read_run_state(project, "CAD-152") is RunState.todo


def test_read_run_state_raises_path_traversal_for_dot_ids() -> None:
    # The single-ticket read keeps the hard traversal guard: a bare '.'/'..' id
    # (admitted by TICKET_ID_PATTERN, yet a single-segment traversal) raises.
    adapter, project = _load_with_run_state()
    for bad_id in (".", ".."):
        with pytest.raises(PathTraversal):
            adapter.read_run_state(project, bad_id)


def test_safe_run_state_degrades_dot_ids_to_unknown(tmp_path: Path) -> None:
    # The LIST/DEPS projection probes run-state for EVERY ticket, so a single '.'/'..'
    # id must degrade to unknown rather than raise PathTraversal and 400 the whole
    # request. A valid id still resolves normally (present dir, no marker -> todo).
    run_state_dir = tmp_path / "run-state"
    (run_state_dir / "todo").mkdir(parents=True)
    assert RealFileAdapter._safe_run_state(run_state_dir, "CAD-1") is RunState.todo
    for bad_id in (".", ".."):
        assert RealFileAdapter._safe_run_state(run_state_dir, bad_id) is RunState.unknown


# --------------------------------------------------------------------------- #
# get_roadmap
# --------------------------------------------------------------------------- #


def test_get_roadmap_returns_rendered_roadmap() -> None:
    adapter, project = _load_with_run_state()
    roadmap = adapter.get_roadmap(project)
    assert isinstance(roadmap, Roadmap)
    assert roadmap.path == project.roadmapPath
    assert "# Cadence Roadmap" in roadmap.bodyMarkdown
    assert "<h1>" in roadmap.bodyHtml


def test_get_roadmap_returns_none_when_project_has_no_roadmap() -> None:
    # The malformed fixture has no ROADMAP.md at the root or under docs/, so
    # load_project resolves roadmapPath to None and get_roadmap short-circuits to
    # None — parity with the fake adapter, which pins the same None-path.
    adapter = RealFileAdapter()
    project = adapter.load_project(MALFORMED)
    assert project.roadmapPath is None
    assert adapter.get_roadmap(project) is None


def test_get_roadmap_maps_a_non_utf8_read_to_the_unreadable_envelope(tmp_path: Path) -> None:
    # A discovered ROADMAP.md whose bytes are not valid UTF-8 must surface as the
    # mapped RoadmapUnreadable envelope — like a ticket .md that cannot be decoded —
    # rather than escaping as a raw UnicodeDecodeError / unmapped 500.
    planning = tmp_path / "docs" / "planning"
    planning.mkdir(parents=True)
    (planning / "tickets.json").write_text('{"schemaVersion": 1, "tickets": []}', encoding="utf-8")
    (tmp_path / "ROADMAP.md").write_bytes(b"\xff\xfe not valid utf-8")
    adapter = RealFileAdapter()
    project = adapter.load_project(tmp_path)
    assert project.roadmapPath is not None
    with pytest.raises(RoadmapUnreadable) as excinfo:
        adapter.get_roadmap(project)
    assert excinfo.value.code == "roadmap_unreadable"
    assert excinfo.value.status == 500


# --------------------------------------------------------------------------- #
# malformed manifest
# --------------------------------------------------------------------------- #


def test_load_project_succeeds_on_malformed_manifest() -> None:
    # Discovery only checks the manifest file EXISTS; it is not parsed here, so
    # loading a project whose manifest is invalid JSON still succeeds.
    project = RealFileAdapter().load_project(MALFORMED)
    assert project.ticketsManifestPath.is_file()


def test_list_tickets_raises_on_malformed_manifest() -> None:
    adapter = RealFileAdapter()
    project = adapter.load_project(MALFORMED)
    with pytest.raises(MalformedManifest):
        adapter.list_tickets(project)


# --------------------------------------------------------------------------- #
# search_tickets — body scan, tolerant enrichment, run-state-resolved summaries
# --------------------------------------------------------------------------- #


def test_search_tickets_finds_a_term_appearing_only_in_a_ticket_body() -> None:
    # 'compensating' appears only in CAD-100's rendered body ("undo = compensating
    # event"), not in any ticket's id/title/provides — so a hit proves the adapter
    # actually reads and scans the on-disk .md bodies.
    adapter, project = _load_with_run_state()
    hits = adapter.search_tickets(project, "compensating")
    assert [hit.ticket.id for hit in hits] == ["CAD-100"]
    assert isinstance(hits[0], SearchHit)
    assert hits[0].matchedFields == ["bodyMarkdown"]
    # The summary carries the real run-state resolved from the run-state dir.
    assert hits[0].ticket.runState is RunState.merged


def test_search_tickets_ranks_id_title_matches_above_body_matches() -> None:
    # 'digest' is in CAD-131's title/body; a title hit must outweigh any body-only hit.
    adapter, project = _load_with_run_state()
    hits = adapter.search_tickets(project, "digest")
    assert hits[0].ticket.id == "CAD-131"
    assert "title" in hits[0].matchedFields


def test_search_tickets_blank_query_returns_empty() -> None:
    adapter, project = _load_with_run_state()
    assert adapter.search_tickets(project, "   ") == []


def test_search_tickets_limit_truncates_results() -> None:
    # 'streak' appears across several tickets; limit=1 keeps only the top hit.
    adapter, project = _load_with_run_state()
    all_hits = adapter.search_tickets(project, "streak")
    assert len(all_hits) > 1
    limited = adapter.search_tickets(project, "streak", limit=1)
    assert len(limited) == 1
    assert limited[0].ticket.id == all_hits[0].ticket.id


def test_search_tickets_tolerates_a_missing_ticket_md(tmp_path: Path) -> None:
    # Copy the fixture to a tmp dir and delete ONE ticket's .md; search must still
    # return hits for the rest rather than failing the whole scan. 'streak' matches
    # tickets beyond the deleted CAD-100, so results survive.
    project_root = tmp_path / "project"
    shutil.copytree(WITH_RUN_STATE, project_root)
    (project_root / "docs" / "planning" / "tickets" / "CAD-100.md").unlink()
    adapter = RealFileAdapter()
    project = adapter.load_project(project_root)
    hits = adapter.search_tickets(project, "streak")
    ids = {hit.ticket.id for hit in hits}
    assert ids  # non-empty: the scan degraded the missing .md to an empty body
    # CAD-118's title/provides mention streaks and its .md is intact.
    assert "CAD-118" in ids


def test_search_tickets_matches_manifest_only_ticket_via_id_when_body_missing(
    tmp_path: Path,
) -> None:
    # With CAD-100's .md deleted, a query for its id still matches (id comes from
    # the manifest stub, independent of the tolerantly-emptied body).
    project_root = tmp_path / "project"
    shutil.copytree(WITH_RUN_STATE, project_root)
    (project_root / "docs" / "planning" / "tickets" / "CAD-100.md").unlink()
    adapter = RealFileAdapter()
    project = adapter.load_project(project_root)
    hits = adapter.search_tickets(project, "CAD-100")
    assert any(hit.ticket.id == "CAD-100" for hit in hits)


def test_search_tickets_tolerates_an_unreadable_md_and_logs_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # An *unreadable* .md (non-UTF-8 bytes -> TicketFileUnreadable) is a genuine
    # data problem, not the routine missing-file case: the scan still degrades it
    # to an empty body and returns hits for the rest, but — unlike a legitimately
    # absent .md — it leaves a WARNING trace so the corruption is observable.
    project_root = tmp_path / "project"
    shutil.copytree(WITH_RUN_STATE, project_root)
    (project_root / "docs" / "planning" / "tickets" / "CAD-100.md").write_bytes(
        b"\xff\xfe not valid utf-8"
    )
    adapter = RealFileAdapter()
    project = adapter.load_project(project_root)
    with caplog.at_level("WARNING", logger="factory_console.file_adapter.real"):
        hits = adapter.search_tickets(project, "streak")
    assert "CAD-118" in {hit.ticket.id for hit in hits}  # the intact tickets still match
    assert any(
        "CAD-100" in record.getMessage() and record.levelname == "WARNING"
        for record in caplog.records
    )
