"""The console read against a project laid out the way the factory lays one out.

EVERY TEST HERE WOULD HAVE FAILED BEFORE THE FIX, and none of the 1182 that
already existed did — because the other fixtures were authored to match the
reader's assumptions rather than the producer's output, so reader and fixtures
agreed and neither agreed with reality. Four views were broken against every real
App Factory project:

* ticket detail 404'd for **every** ticket (resolver built ``<ticketsDir>/<id>.md``;
  real files live at ``<ticketsDir>/<milestone>/<id>-<slug>.md``, and the manifest
  said so in a ``path`` field nothing read);
* the roadmap reported "no roadmap" (probe never looked in ``docs/planning/``,
  where it had just found ``tickets.json``);
* the graph returned every ticket as an unconnected node (manifest read
  ``dependsOn``; the factory writes ``depends_on``);
* dep-neighborhood reported ``depCount: 0`` for the same reason.

Measured on the factory-console repository itself: 101 nodes, 0 edges, 101 404s.

These assertions are deliberately about a REAL layout rather than a synthesized
one. ``tests/fixtures/projects/factory_layout/`` mirrors what the factory writes;
its README says why it must not be normalized to look like its neighbours.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from factory_console.domain.write import TicketDraft, TicketEdit
from factory_console.file_adapter.real import RealFileAdapter
from factory_console.file_adapter.real_writer import RealFileWriter
from factory_console.services.write_service import WriteConflict, WriteService

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "projects" / "factory_layout"


@pytest.fixture
def project():
    return RealFileAdapter().load_project(FIXTURE)


# --- the ticket path the manifest declares ----------------------------------


def test_ticket_under_a_milestone_directory_is_found(project) -> None:
    """The bug that 404'd all 101 tickets on the real repository."""
    ticket = RealFileAdapter().get_ticket(project, "T01")
    assert ticket is not None, "a ticket whose manifest path is nested must resolve"
    assert "Base." in ticket.bodyMarkdown


def test_the_resolved_path_is_the_one_the_manifest_declared(project) -> None:
    ticket = RealFileAdapter().get_ticket(project, "T03")
    assert ticket is not None
    assert (
        ticket.filePath == (FIXTURE / "docs/planning/tickets/v2/T03-a-later-milestone.md").resolve()
    )


def test_a_later_milestone_directory_resolves_too(project) -> None:
    """Not just the first directory probed — the path is read, not guessed."""
    ticket = RealFileAdapter().get_ticket(project, "T03")
    assert ticket is not None
    assert "The view." in ticket.bodyMarkdown


def test_an_id_absent_from_the_manifest_is_still_None(project) -> None:
    """The converse: honouring `path` must not make unknown ids resolve."""
    assert RealFileAdapter().get_ticket(project, "T99") is None


# --- the dependency key the factory writes ----------------------------------


def test_depends_on_is_read_so_the_graph_has_edges(project) -> None:
    """101 nodes and 0 edges was the symptom; this is the cause."""
    graph = RealFileAdapter().get_graph(project)
    assert len(graph.nodes) == 3
    edges = {(e.source, e.target) for e in graph.edges}
    assert edges == {("T02", "T01"), ("T03", "T01"), ("T03", "T02")}, (
        "every dependency in the manifest must become an edge"
    )


def test_dep_neighborhood_counts_are_not_zero(project) -> None:
    deps = RealFileAdapter().get_deps(project, "T03")
    assert deps is not None
    assert {d.id for d in deps.directDeps} == {"T01", "T02"}


def test_dependents_resolve_in_the_other_direction(project) -> None:
    deps = RealFileAdapter().get_deps(project, "T01")
    assert deps is not None
    assert {d.id for d in deps.directDependents} == {"T02", "T03"}


# --- the roadmap location the factory uses ----------------------------------


def test_roadmap_beside_the_manifest_is_discovered(project) -> None:
    """`roadmapPath: null` and "This project has no roadmap" was the symptom."""
    assert project.roadmapPath is not None, "docs/planning/ROADMAP.md must be found"
    assert project.roadmapPath.name == "ROADMAP.md"
    assert project.roadmapPath.parent.name == "planning"


def test_the_discovered_roadmap_actually_parses(project) -> None:
    roadmap = RealFileAdapter().get_roadmap(project)
    assert roadmap is not None
    names = [m.name for m in roadmap.milestones]
    assert any(n.startswith("v1") for n in names)
    assert any(n.startswith("v2") for n in names)


def test_checkbox_state_survives_the_parse(project) -> None:
    """The roadmap view renders these; a milestone with no done-state is useless."""
    roadmap = RealFileAdapter().get_roadmap(project)
    assert roadmap is not None
    v1 = next(m for m in roadmap.milestones if m.name.startswith("v1"))
    assert [i.done for i in v1.items] == [True, False]


# --- containment is not weakened by honouring the manifest ------------------


def test_a_manifest_path_escaping_the_root_is_refused(tmp_path: Path) -> None:
    """`path` is repository data, and data is still not trusted.

    A manifest that points a ticket at /etc/passwd must be refused exactly as a
    traversing id is. Honouring `path` widens WHERE a ticket may live inside the
    project; it must not widen whether it may leave.
    """
    import json
    import shutil

    from factory_console.file_adapter.path_safety import PathTraversal

    root = tmp_path / "evil"
    shutil.copytree(FIXTURE, root)
    manifest_path = root / "docs/planning/tickets.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["tickets"][0]["path"] = "../../../../../../etc/passwd"
    manifest_path.write_text(json.dumps(manifest))

    adapter = RealFileAdapter()
    project = adapter.load_project(root)
    with pytest.raises(PathTraversal):
        adapter.get_ticket(project, "T01")


def test_a_symlinked_manifest_path_escaping_the_root_is_refused(tmp_path: Path) -> None:
    """The resolved path is checked, so a symlink cannot smuggle one out either."""
    import json
    import shutil

    from factory_console.file_adapter.path_safety import PathTraversal

    root = tmp_path / "linky"
    shutil.copytree(FIXTURE, root)
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    link = root / "docs/planning/tickets/v1/T01-the-base-ticket.md"
    link.unlink()
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("platform does not support symlinks")

    manifest_path = root / "docs/planning/tickets.json"
    json.loads(manifest_path.read_text())  # unchanged: the path is legitimate, the target is not

    adapter = RealFileAdapter()
    project = adapter.load_project(root)
    with pytest.raises(PathTraversal):
        adapter.get_ticket(project, "T01")


# --- the flat layout the older fixtures use must keep working ---------------


def test_a_manifest_without_path_still_falls_back_to_the_flat_layout() -> None:
    """Backwards compatibility, asserted rather than assumed.

    A hand-written manifest need not declare `path`. The old `<ticketsDir>/<id>.md`
    derivation remains the fallback, so the existing fixtures — and any project
    laid out the way this codebase used to assume — keep reading.
    """
    flat = Path(__file__).resolve().parents[1] / "fixtures" / "projects" / "with_run_state"
    adapter = RealFileAdapter()
    project = adapter.load_project(flat)
    summaries = adapter.list_tickets(project)
    assert summaries, "the flat fixture must still list tickets"
    ticket = adapter.get_ticket(project, summaries[0].id)
    assert ticket is not None, "a manifest with no `path` must still resolve its ticket"


# --- the WRITE side of the same defect --------------------------------------
#
# Everything above is the READ path learning to honour what the manifest declares.
# The write path never learned: it re-derived `<ticketsDir>/<id>.md` regardless, so
# against this same fixture an edit merged nothing and wrote a NEW orphan file
# beside the real one while answering `applied=true`, and a delete unlinked a path
# that was never there. Search had the same gap on the read side — it passed only
# the id, so every body read missed and full-text search matched nothing at all.


@pytest.fixture
def writable_project(tmp_path: Path):
    """A writable copy of the factory-layout fixture, loaded as a project.

    The fixture's ``.factory/run-state.json`` is dropped: it marks tickets ``merged``
    and the write gate (rightly) refuses those, which is a different rule from the
    one under test here. Without the file every ticket reads ``unknown``, which the
    edit and delete allowlists both admit — so these tests exercise the PATH and KEY
    behaviour and nothing else. Mutability itself is covered in ``test_write_gate``.
    """
    root = tmp_path / "project"
    shutil.copytree(FIXTURE, root)
    (root / ".factory" / "run-state.json").unlink()
    return RealFileAdapter().load_project(root)


def _manifest_entry(project, ticket_id: str) -> dict:
    entries = json.loads(project.ticketsManifestPath.read_text(encoding="utf-8"))["tickets"]
    return next(entry for entry in entries if entry["id"] == ticket_id)


def test_search_matches_a_word_that_appears_only_in_a_nested_body(project) -> None:
    """Full-text search read every body from the flat path, so it matched nothing."""
    hits = RealFileAdapter().search_tickets(project, "Base.")
    assert [hit.ticket.id for hit in hits] == ["T01"], (
        "a body-only term must match the ticket whose nested .md contains it"
    )


def test_an_edit_rewrites_the_declared_file_and_creates_no_orphan(writable_project) -> None:
    """The edit lands in the manifest-declared file, not in a new flat one."""
    declared = writable_project.rootPath / "docs/planning/tickets/v1/T01-the-base-ticket.md"
    before = {path for path in writable_project.rootPath.rglob("*.md")}

    result = RealFileWriter().edit_ticket(
        writable_project,
        "T01",
        TicketEdit(title="Retitled", bodyMarkdown="Rewritten body.\n"),
    )

    assert result.applied is True
    assert "Rewritten body." in declared.read_text(encoding="utf-8"), (
        "the edit must land in the file the manifest declares"
    )
    assert {path for path in writable_project.rootPath.rglob("*.md")} == before, (
        "no orphan <ticketsDir>/<id>.md may be created beside the real file"
    )


def test_an_edit_that_clears_dependencies_is_actually_read_back_cleared(writable_project) -> None:
    """`dependsOn` was written beside a surviving `depends_on`, which still won."""
    RealFileWriter().edit_ticket(
        writable_project,
        "T02",
        TicketEdit(title="Depends on T01, in the same milestone", dependsOn=[], bodyMarkdown="x\n"),
    )

    entry = _manifest_entry(writable_project, "T02")
    assert "dependsOn" not in entry, "an entry must never carry both spellings of the key"
    assert entry["depends_on"] == [], "the producer's key must hold the edited value"

    neighborhood = RealFileAdapter().get_deps(writable_project, "T02")
    assert neighborhood is not None
    assert neighborhood.directDeps == [], "the cleared dependency must not come back on read"


def test_an_edit_that_sets_dependencies_is_read_back_set(writable_project) -> None:
    RealFileWriter().edit_ticket(
        writable_project,
        "T01",
        TicketEdit(
            title="The base ticket everything depends on", dependsOn=["T02"], bodyMarkdown="x\n"
        ),
    )
    neighborhood = RealFileAdapter().get_deps(writable_project, "T01")
    assert neighborhood is not None
    assert [dep.id for dep in neighborhood.directDeps] == ["T02"]


def test_a_delete_removes_the_declared_file(writable_project) -> None:
    declared = writable_project.rootPath / "docs/planning/tickets/v2/T03-a-later-milestone.md"
    assert declared.exists()

    RealFileWriter().delete_ticket(writable_project, "T03")

    assert not declared.exists(), "delete must unlink the file the manifest declared"
    entries = json.loads(writable_project.ticketsManifestPath.read_text(encoding="utf-8"))[
        "tickets"
    ]
    assert [entry["id"] for entry in entries] == ["T01", "T02"]


def test_a_created_ticket_uses_the_producers_dependency_key(writable_project) -> None:
    """A console-created ticket must read back the way a factory-created one does."""
    RealFileWriter().create_ticket(
        writable_project,
        TicketDraft(id="T04", title="New", dependsOn=["T01"], bodyMarkdown="body\n"),
    )
    entry = _manifest_entry(writable_project, "T04")
    assert entry["depends_on"] == ["T01"]
    assert "dependsOn" not in entry


# --- a manifest entry whose body file is gone -------------------------------


def test_an_orphan_manifest_entry_can_still_be_deleted(writable_project) -> None:
    """The pre-delete re-read 404'd, leaving the entry with no way to remove it."""
    (writable_project.rootPath / "docs/planning/tickets/v2/T03-a-later-milestone.md").unlink()
    project = RealFileAdapter().load_project(writable_project.rootPath)
    service = WriteService(RealFileWriter(), RealFileAdapter())

    result = service.delete(project, "T03", dry_run=False)

    assert result.applied is True
    entries = json.loads(project.ticketsManifestPath.read_text(encoding="utf-8"))["tickets"]
    assert [entry["id"] for entry in entries] == ["T01", "T02"]


def test_a_create_colliding_with_an_orphan_entry_is_a_conflict_not_a_404(
    writable_project,
) -> None:
    """`get_ticket` as an existence probe turned a 409 collision into a 404."""
    (writable_project.rootPath / "docs/planning/tickets/v2/T03-a-later-milestone.md").unlink()
    project = RealFileAdapter().load_project(writable_project.rootPath)
    service = WriteService(RealFileWriter(), RealFileAdapter())

    with pytest.raises(WriteConflict):
        service.create(
            project,
            TicketDraft(id="T03", title="Colliding", bodyMarkdown="x\n"),
            dry_run=False,
        )
