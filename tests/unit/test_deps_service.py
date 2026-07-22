"""Unit tests for :class:`DepsService` over the in-memory :class:`FakeFileAdapter`.

Pin the one behavior the HTTP handler delegates to: resolving a ticket's
dependency neighborhood and raising :class:`TicketNotFound` for an unseeded id.
The neighborhood arrives fully built (and run-state-resolved) from the shared
projection, so the service only delegates and maps the absent-id ``None`` to the
domain error — there is no fallback composition and no run-state join to test
here. Deterministic and I/O-free: the fake answers every call from seeded
in-memory data. Assertions are on ids / order, never object identity.

The seeded dependency graph mirrors the ``with_run_state`` fixture's shape:

* ``D-100`` — no deps; depended on by ``D-118`` and ``D-125``.
* ``D-118`` — depends on ``D-100``.
* ``D-125`` — depends on ``D-100`` + ``D-118``; depended on by ``D-131`` + ``D-140``.
* ``D-131`` — depends on ``D-125`` + a dangling ``D-207-nonexistent``; no dependents.
* ``D-140`` — depends on ``D-125``.
"""

from datetime import datetime
from pathlib import Path

import pytest

from factory_console.domain import Project, RunState, Ticket
from factory_console.file_adapter import FakeFileAdapter
from factory_console.services.deps_service import DepsService
from factory_console.services.ticket_service import TicketNotFound


def _make_project() -> Project:
    return Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        roadmapPath=Path("/proj/ROADMAP.md"),
        runStateDir=Path("/proj/.factory/run-state"),
        discoveredAt=datetime(2026, 7, 21, 12, 0, 0),
    )


def _make_ticket(ticket_id: str, *, depends_on: list[str]) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=f"Ticket {ticket_id}",
        status="todo",
        track="backend",
        milestone="MVP",
        dependsOn=depends_on,
        filePath=Path(f"/proj/docs/planning/tickets/{ticket_id}.md"),
        bodyMarkdown=f"# {ticket_id}",
        bodyHtml=f"<h1>{ticket_id}</h1>",
        raw={"id": ticket_id},
    )


def _tickets() -> list[Ticket]:
    return [
        _make_ticket("D-100", depends_on=[]),
        _make_ticket("D-118", depends_on=["D-100"]),
        _make_ticket("D-125", depends_on=["D-100", "D-118"]),
        _make_ticket("D-131", depends_on=["D-125", "D-207-nonexistent"]),
        _make_ticket("D-140", depends_on=["D-125"]),
    ]


_RUN_STATES = {
    "D-100": RunState.merged,
    "D-118": RunState.ready,
    "D-125": RunState.in_flight,
}


def _service() -> tuple[DepsService, Project]:
    project = _make_project()
    fake = FakeFileAdapter(project=project, tickets=_tickets(), run_states=_RUN_STATES)
    return DepsService(fake), project


def _ids(summaries: list) -> list[str]:
    return [summary.id for summary in summaries]


# --------------------------------------------------------------------------- #
# happy path — resolved direct deps AND dependents (ids + order)
# --------------------------------------------------------------------------- #


def test_get_neighborhood_resolves_direct_deps_and_dependents() -> None:
    service, project = _service()
    neighborhood = service.get_neighborhood(project, "D-125")
    assert neighborhood.ticket.id == "D-125"
    assert _ids(neighborhood.directDeps) == ["D-100", "D-118"]
    assert _ids(neighborhood.directDependents) == ["D-131", "D-140"]
    assert neighborhood.unresolvedDeps == []


def test_get_neighborhood_carries_seeded_run_state_without_join() -> None:
    # The projection resolves run-state on every summary, so the service adds no
    # join: the subject and its edges arrive already run-state-resolved.
    service, project = _service()
    neighborhood = service.get_neighborhood(project, "D-125")
    assert neighborhood.ticket.runState is RunState.in_flight
    deps_by_id = {dep.id: dep.runState for dep in neighborhood.directDeps}
    assert deps_by_id == {"D-100": RunState.merged, "D-118": RunState.ready}


# --------------------------------------------------------------------------- #
# unresolved dep — dangling id lands in unresolvedDeps, not directDeps
# --------------------------------------------------------------------------- #


def test_get_neighborhood_surfaces_unresolved_dep_edge() -> None:
    service, project = _service()
    neighborhood = service.get_neighborhood(project, "D-131")
    assert _ids(neighborhood.directDeps) == ["D-125"]
    assert neighborhood.unresolvedDeps == ["D-207-nonexistent"]
    assert neighborhood.directDependents == []


# --------------------------------------------------------------------------- #
# no-deps ticket — empty directDeps/unresolvedDeps (still has dependents here)
# --------------------------------------------------------------------------- #


def test_get_neighborhood_for_no_deps_ticket_has_empty_deps() -> None:
    service, project = _service()
    neighborhood = service.get_neighborhood(project, "D-100")
    assert neighborhood.directDeps == []
    assert neighborhood.unresolvedDeps == []
    # D-100 declares no deps but is depended on by D-118 and D-125 (seeded order).
    assert _ids(neighborhood.directDependents) == ["D-118", "D-125"]


# --------------------------------------------------------------------------- #
# unknown id — None from the adapter becomes TicketNotFound (404)
# --------------------------------------------------------------------------- #


def test_get_neighborhood_raises_ticket_not_found_for_unseeded_id() -> None:
    service, project = _service()
    with pytest.raises(TicketNotFound) as excinfo:
        service.get_neighborhood(project, "D-999")
    assert excinfo.value.code == "ticket_not_found"
    assert excinfo.value.status == 404
