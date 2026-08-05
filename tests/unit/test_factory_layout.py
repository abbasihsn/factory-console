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

from pathlib import Path

import pytest

from factory_console.file_adapter.real import RealFileAdapter

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
    assert ticket.filePath == (FIXTURE / "docs/planning/tickets/v2/T03-a-later-milestone.md").resolve()


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
