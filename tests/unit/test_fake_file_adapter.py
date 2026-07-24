"""Unit tests for the in-memory :class:`FakeFileAdapter`.

These pin the read-only FileAdapter contract the backend codes against before
the real adapter exists: the ``@runtime_checkable`` ``isinstance`` gate, the six
methods' shapes, and the two derived semantics that must never drift between the
list view and the dependency view — ``depCount`` counts ALL declared deps
(dangling included) while the reverse index (``dependentCount`` /
``directDependents``) counts only OTHER seeded tickets. Deterministic and
I/O-free — pydantic + stdlib only.
"""

from datetime import datetime
from pathlib import Path

from factory_console.domain import (
    Project,
    Roadmap,
    RunState,
    Ticket,
    TicketSummary,
)
from factory_console.domain.graph import TicketGraph
from factory_console.domain.search import SearchHit
from factory_console.file_adapter import FakeFileAdapter, FileAdapter


class _PartialAdapter:
    """Implements only ONE of the six methods — proves the runtime check is real."""

    def load_project(self, root: Path) -> Project:  # pragma: no cover - never called
        raise NotImplementedError


def _make_project() -> Project:
    return Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        roadmapPath=Path("/proj/ROADMAP.md"),
        runStateDir=Path("/proj/.factory/run-state"),
        discoveredAt=datetime(2026, 7, 20, 12, 0, 0),
    )


def _make_ticket(
    ticket_id: str,
    *,
    depends_on: list[str] | None = None,
    status: str = "open",
    track: str | None = "file-adapter",
    milestone: str | None = "MVP",
) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=f"Ticket {ticket_id}",
        status=status,
        track=track,
        milestone=milestone,
        dependsOn=depends_on or [],
        filePath=Path(f"/proj/docs/planning/tickets/{ticket_id}.md"),
        bodyMarkdown=f"# {ticket_id}",
        bodyHtml=f"<h1>{ticket_id}</h1>",
        raw={"id": ticket_id},
    )


def _abc_tickets() -> list[Ticket]:
    # Dependency graph A<-B, A<-C, B<-C  (edge X<-Y means Y dependsOn X):
    #   T-A: (no deps)   T-B: [T-A]   T-C: [T-A, T-B]
    # so dependents: T-A -> {T-B, T-C}, T-B -> {T-C}, T-C -> {}.
    return [
        _make_ticket("T-A"),
        _make_ticket("T-B", depends_on=["T-A"]),
        _make_ticket("T-C", depends_on=["T-A", "T-B"]),
    ]


def _make_roadmap() -> Roadmap:
    return Roadmap(
        path=Path("/proj/ROADMAP.md"),
        bodyMarkdown="# Roadmap",
        bodyHtml="<h1>Roadmap</h1>",
    )


def _summary_by_id(summaries: list[TicketSummary], ticket_id: str) -> TicketSummary:
    return next(summary for summary in summaries if summary.id == ticket_id)


# --------------------------------------------------------------------------- #
# runtime_checkable Protocol gate
# --------------------------------------------------------------------------- #


def test_fake_satisfies_runtime_checkable_file_adapter() -> None:
    fake = FakeFileAdapter(project=_make_project(), tickets=_abc_tickets())
    assert isinstance(fake, FileAdapter)


def test_object_without_the_six_methods_is_not_a_file_adapter() -> None:
    # The runtime check is real, not vacuous: a bare object and a partial
    # implementation (missing five of the six methods) are both rejected.
    assert not isinstance(object(), FileAdapter)
    assert not isinstance(_PartialAdapter(), FileAdapter)


# --------------------------------------------------------------------------- #
# load_project
# --------------------------------------------------------------------------- #


def test_load_project_returns_seeded_project_ignoring_root() -> None:
    project = _make_project()
    fake = FakeFileAdapter(project=project, tickets=_abc_tickets())
    # The fake is pre-seeded; the root argument is accepted but ignored.
    assert fake.load_project(Path("/some/other/ignored/root")) is project


# --------------------------------------------------------------------------- #
# list_tickets — projection, order, run-state, and edge counts
# --------------------------------------------------------------------------- #


def test_list_tickets_projects_every_ticket_in_seeded_order() -> None:
    fake = FakeFileAdapter(project=_make_project(), tickets=_abc_tickets())
    summaries = fake.list_tickets(_make_project())
    assert [summary.id for summary in summaries] == ["T-A", "T-B", "T-C"]
    assert all(isinstance(summary, TicketSummary) for summary in summaries)
    # Carried-over fields come straight from the seeded ticket.
    t_b = _summary_by_id(summaries, "T-B")
    assert t_b.title == "Ticket T-B"
    assert t_b.status == "open"
    assert t_b.track == "file-adapter"
    assert t_b.milestone == "MVP"


def test_list_tickets_resolves_run_state_defaulting_to_unknown() -> None:
    run_states = {"T-A": RunState.merged, "T-B": RunState.in_flight}
    fake = FakeFileAdapter(project=_make_project(), tickets=_abc_tickets(), run_states=run_states)
    summaries = fake.list_tickets(_make_project())
    assert _summary_by_id(summaries, "T-A").runState is RunState.merged
    assert _summary_by_id(summaries, "T-B").runState is RunState.in_flight
    # T-C has no seeded run-state -> unknown.
    assert _summary_by_id(summaries, "T-C").runState is RunState.unknown


def test_dep_and_dependent_counts_use_the_reverse_index() -> None:
    fake = FakeFileAdapter(project=_make_project(), tickets=_abc_tickets())
    summaries = fake.list_tickets(_make_project())
    counts = {summary.id: (summary.depCount, summary.dependentCount) for summary in summaries}
    # (depCount, dependentCount)
    assert counts["T-A"] == (0, 2)  # nothing declared; T-B and T-C depend on it
    assert counts["T-B"] == (1, 1)  # deps [T-A]; only T-C depends on it
    assert counts["T-C"] == (2, 0)  # deps [T-A, T-B]; nothing depends on it


def test_dep_count_includes_dangling_declared_dependencies() -> None:
    # depCount is len(dependsOn): a dangling id (no seeded ticket) still counts.
    tickets = [_make_ticket("T-X", depends_on=["T-A", "GHOST-1"])]
    fake = FakeFileAdapter(project=_make_project(), tickets=tickets)
    summary = fake.list_tickets(_make_project())[0]
    assert summary.depCount == 2  # both T-A (dangling here too) and GHOST-1 counted
    assert summary.dependentCount == 0


# --------------------------------------------------------------------------- #
# get_ticket
# --------------------------------------------------------------------------- #


def test_get_ticket_returns_seeded_ticket_by_id() -> None:
    tickets = _abc_tickets()
    fake = FakeFileAdapter(project=_make_project(), tickets=tickets)
    got = fake.get_ticket(_make_project(), "T-B")
    assert got is tickets[1]
    assert got.id == "T-B"


def test_get_ticket_returns_none_for_unknown_id() -> None:
    fake = FakeFileAdapter(project=_make_project(), tickets=_abc_tickets())
    assert fake.get_ticket(_make_project(), "NOPE") is None


# --------------------------------------------------------------------------- #
# get_deps — neighborhood shape, order, unresolved, dependents
# --------------------------------------------------------------------------- #


def test_get_deps_resolves_direct_deps_in_depends_on_order() -> None:
    fake = FakeFileAdapter(project=_make_project(), tickets=_abc_tickets())
    neighborhood = fake.get_deps(_make_project(), "T-C")
    assert neighborhood is not None
    assert neighborhood.ticket.id == "T-C"
    assert [dep.id for dep in neighborhood.directDeps] == ["T-A", "T-B"]
    assert neighborhood.unresolvedDeps == []
    assert neighborhood.directDependents == []


def test_get_deps_reverse_indexes_direct_dependents_in_seeded_order() -> None:
    fake = FakeFileAdapter(project=_make_project(), tickets=_abc_tickets())
    neighborhood = fake.get_deps(_make_project(), "T-A")
    assert neighborhood is not None
    assert neighborhood.directDeps == []
    assert [dep.id for dep in neighborhood.directDependents] == ["T-B", "T-C"]
    assert neighborhood.ticket.dependentCount == 2


def test_get_deps_marks_unresolved_deps_and_keeps_resolved_ones() -> None:
    tickets = [
        _make_ticket("T-A"),
        _make_ticket("T-Z", depends_on=["T-A", "GHOST-1", "GHOST-2"]),
    ]
    fake = FakeFileAdapter(project=_make_project(), tickets=tickets)
    neighborhood = fake.get_deps(_make_project(), "T-Z")
    assert neighborhood is not None
    assert [dep.id for dep in neighborhood.directDeps] == ["T-A"]
    assert neighborhood.unresolvedDeps == ["GHOST-1", "GHOST-2"]
    assert neighborhood.ticket.depCount == 3  # all three declared deps counted


def test_get_deps_returns_none_for_unknown_id() -> None:
    fake = FakeFileAdapter(project=_make_project(), tickets=_abc_tickets())
    assert fake.get_deps(_make_project(), "NOPE") is None


def test_duplicate_depends_on_does_not_inflate_the_reverse_index() -> None:
    # A ticket that declares the same dependency twice must count as ONE dependent
    # of that id — the reverse index counts distinct dependent TICKETS, not declared
    # edges — even though its own depCount still counts both declared edges.
    fake = FakeFileAdapter(
        project=_make_project(),
        tickets=[_make_ticket("T-A"), _make_ticket("T-D", depends_on=["T-A", "T-A"])],
    )
    summaries = fake.list_tickets(_make_project())
    assert _summary_by_id(summaries, "T-A").dependentCount == 1  # T-D counted once
    assert _summary_by_id(summaries, "T-D").depCount == 2  # both declared edges counted
    neighborhood = fake.get_deps(_make_project(), "T-A")
    assert neighborhood is not None
    assert [dep.id for dep in neighborhood.directDependents] == ["T-D"]  # no duplicate link


def test_duplicate_depends_on_does_not_repeat_a_forward_neighbor() -> None:
    # The forward side of the neighborhood must name each neighbour ONCE too, in
    # first-seen order: a repeated id in either directDeps or unresolvedDeps is a
    # duplicate key for the deps route's keyed {#each}, which crashes the page.
    tickets = [
        _make_ticket("T-A"),
        _make_ticket("T-D", depends_on=["T-A", "GHOST-1", "T-A", "GHOST-1"]),
    ]
    fake = FakeFileAdapter(project=_make_project(), tickets=tickets)
    neighborhood = fake.get_deps(_make_project(), "T-D")
    assert neighborhood is not None
    assert [dep.id for dep in neighborhood.directDeps] == ["T-A"]
    assert neighborhood.unresolvedDeps == ["GHOST-1"]
    assert neighborhood.ticket.depCount == 4  # all four declared edges still counted


def test_self_dependency_counts_toward_dep_count_but_not_dependents() -> None:
    # A ticket that lists itself: the self-edge counts as a declared dependency
    # (depCount) and resolves in directDeps, but the reverse index never treats a
    # ticket as its own dependent (dependentCount stays 0; directDependents empty).
    fake = FakeFileAdapter(
        project=_make_project(), tickets=[_make_ticket("T-S", depends_on=["T-S"])]
    )
    summary = fake.list_tickets(_make_project())[0]
    assert summary.depCount == 1
    assert summary.dependentCount == 0
    neighborhood = fake.get_deps(_make_project(), "T-S")
    assert neighborhood is not None
    assert [dep.id for dep in neighborhood.directDeps] == ["T-S"]
    assert neighborhood.directDependents == []


# --------------------------------------------------------------------------- #
# read_run_state
# --------------------------------------------------------------------------- #


def test_read_run_state_returns_seeded_state_and_defaults_unknown() -> None:
    fake = FakeFileAdapter(
        project=_make_project(),
        tickets=_abc_tickets(),
        run_states={"T-A": RunState.ready},
    )
    project = _make_project()
    assert fake.read_run_state(project, "T-A") is RunState.ready
    # Seeded ticket without a seeded run-state -> unknown.
    assert fake.read_run_state(project, "T-B") is RunState.unknown
    # Entirely unknown id -> unknown (method is non-optional).
    assert fake.read_run_state(project, "NOPE") is RunState.unknown


def test_run_states_none_defaults_every_ticket_to_unknown() -> None:
    fake = FakeFileAdapter(project=_make_project(), tickets=_abc_tickets(), run_states=None)
    summaries = fake.list_tickets(_make_project())
    assert all(summary.runState is RunState.unknown for summary in summaries)
    assert fake.read_run_state(_make_project(), "T-A") is RunState.unknown


# --------------------------------------------------------------------------- #
# get_roadmap
# --------------------------------------------------------------------------- #


def test_get_roadmap_returns_seeded_roadmap() -> None:
    roadmap = _make_roadmap()
    fake = FakeFileAdapter(project=_make_project(), tickets=_abc_tickets(), roadmap=roadmap)
    assert fake.get_roadmap(_make_project()) is roadmap


def test_get_roadmap_returns_none_when_seeded_without_one() -> None:
    fake = FakeFileAdapter(project=_make_project(), tickets=_abc_tickets())
    assert fake.get_roadmap(_make_project()) is None


# --------------------------------------------------------------------------- #
# read-only / side-effect-free guarantee
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# search_tickets — ranked hits with run-state-resolved summaries
# --------------------------------------------------------------------------- #


def _searchable_tickets() -> list[Ticket]:
    # T-A title matches 'streak'; T-B body matches 'streak'; T-C matches nothing.
    return [
        Ticket(
            id="T-A",
            title="Streak service",
            status="open",
            filePath=Path("/proj/docs/planning/tickets/T-A.md"),
            bodyMarkdown="# T-A",
            bodyHtml="",
            raw={"id": "T-A"},
        ),
        Ticket(
            id="T-B",
            title="Weekly digest",
            status="open",
            filePath=Path("/proj/docs/planning/tickets/T-B.md"),
            bodyMarkdown="a body mentioning the streak fold",
            bodyHtml="",
            raw={"id": "T-B"},
        ),
        Ticket(
            id="T-C",
            title="Unrelated",
            status="open",
            filePath=Path("/proj/docs/planning/tickets/T-C.md"),
            bodyMarkdown="nothing here",
            bodyHtml="",
            raw={"id": "T-C"},
        ),
    ]


def test_search_tickets_returns_ranked_hits_with_resolved_summaries() -> None:
    fake = FakeFileAdapter(
        project=_make_project(),
        tickets=_searchable_tickets(),
        run_states={"T-A": RunState.merged, "T-B": RunState.in_flight},
    )
    hits = fake.search_tickets(_make_project(), "streak")
    assert all(isinstance(hit, SearchHit) for hit in hits)
    # Title hit (T-A) outranks the body hit (T-B); T-C scores zero and is dropped.
    assert [hit.ticket.id for hit in hits] == ["T-A", "T-B"]
    assert hits[0].matchedFields == ["title"]
    assert hits[1].matchedFields == ["bodyMarkdown"]
    # The summary carries run-state resolved from the seeded map.
    assert hits[0].ticket.runState is RunState.merged
    assert hits[1].ticket.runState is RunState.in_flight


def test_search_tickets_blank_query_returns_empty() -> None:
    fake = FakeFileAdapter(project=_make_project(), tickets=_searchable_tickets())
    assert fake.search_tickets(_make_project(), "   ") == []


def test_search_tickets_limit_truncates_to_first_n_hits() -> None:
    fake = FakeFileAdapter(project=_make_project(), tickets=_searchable_tickets())
    hits = fake.search_tickets(_make_project(), "streak", limit=1)
    assert [hit.ticket.id for hit in hits] == ["T-A"]


# --------------------------------------------------------------------------- #
# get_graph — whole-project DAG whose node run-state matches list_tickets
# --------------------------------------------------------------------------- #


def test_get_graph_returns_ticket_graph_with_nodes_and_edges() -> None:
    fake = FakeFileAdapter(project=_make_project(), tickets=_abc_tickets())
    graph = fake.get_graph(_make_project())
    assert isinstance(graph, TicketGraph)
    assert [node.id for node in graph.nodes] == ["T-A", "T-B", "T-C"]
    # T-B<-T-A, T-C<-T-A, T-C<-T-B (edge source depends on target).
    assert {(edge.source, edge.target) for edge in graph.edges} == {
        ("T-B", "T-A"),
        ("T-C", "T-A"),
        ("T-C", "T-B"),
    }


def test_get_graph_node_run_state_matches_list_tickets_summaries() -> None:
    # No drift: each node's run-state equals the matching list_tickets summary's.
    run_states = {"T-A": RunState.merged, "T-B": RunState.in_flight}
    fake = FakeFileAdapter(project=_make_project(), tickets=_abc_tickets(), run_states=run_states)
    summaries = {s.id: s.runState for s in fake.list_tickets(_make_project())}
    graph = fake.get_graph(_make_project())
    assert {node.id: node.runState for node in graph.nodes} == summaries


def test_get_graph_keeps_fake_satisfying_the_file_adapter_protocol() -> None:
    fake = FakeFileAdapter(project=_make_project(), tickets=_abc_tickets())
    assert isinstance(fake, FileAdapter)


def test_methods_do_not_mutate_seeded_state() -> None:
    tickets = _abc_tickets()
    fake = FakeFileAdapter(project=_make_project(), tickets=tickets)
    first = fake.list_tickets(_make_project())
    first.append(first[0])  # mutate the returned list
    # A fresh call is unaffected: each call returns a new list over untouched data.
    assert [summary.id for summary in fake.list_tickets(_make_project())] == [
        "T-A",
        "T-B",
        "T-C",
    ]
    # The seeded tickets themselves are untouched.
    assert [ticket.id for ticket in tickets] == ["T-A", "T-B", "T-C"]
