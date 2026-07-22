"""Unit tests for :class:`DepsService` over the in-memory :class:`FakeFileAdapter`.

Pin the one behavior the HTTP handler delegates to: resolving a ticket's
dependency neighborhood via the shared projection and raising
:class:`TicketNotFound` for an unseeded id. ``directDeps`` follow ``dependsOn``
order, ``directDependents`` follow seeded order, and ``unresolvedDeps`` holds the
``dependsOn`` ids with no seeded ticket. Deterministic and I/O-free: the fake
answers every call from seeded in-memory data.
"""

from datetime import datetime
from pathlib import Path

import pytest

from factory_console.domain import Project, Ticket
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


def _make_ticket(ticket_id: str, *, depends_on: list[str] | None = None) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=f"Ticket {ticket_id}",
        status="todo",
        track="backend",
        milestone="MVP",
        dependsOn=depends_on or [],
        filePath=Path(f"/proj/docs/planning/tickets/{ticket_id}.md"),
        bodyMarkdown=f"# {ticket_id}",
        bodyHtml=f"<h1>{ticket_id}</h1>",
        raw={"id": ticket_id},
    )


# A small DAG: T-125 has two resolved deps (T-100, T-118) and two dependents
# (T-131, T-140); T-131 also names a dangling id; T-100 has no deps.
def _tickets() -> list[Ticket]:
    return [
        _make_ticket("T-100"),
        _make_ticket("T-118", depends_on=["T-100"]),
        _make_ticket("T-125", depends_on=["T-100", "T-118"]),
        _make_ticket("T-131", depends_on=["T-125", "T-999-missing"]),
        _make_ticket("T-140", depends_on=["T-125"]),
    ]


def _service() -> tuple[DepsService, Project]:
    project = _make_project()
    fake = FakeFileAdapter(project=project, tickets=_tickets())
    return DepsService(fake), project


def _ids(summaries: list) -> list[str]:
    return [summary.id for summary in summaries]


def test_resolved_deps_and_dependents_neighborhood() -> None:
    service, project = _service()
    neighborhood = service.get_neighborhood(project, "T-125")
    assert neighborhood.ticket.id == "T-125"
    # directDeps follow dependsOn order; directDependents follow seeded order.
    assert _ids(neighborhood.directDeps) == ["T-100", "T-118"]
    assert _ids(neighborhood.directDependents) == ["T-131", "T-140"]
    assert neighborhood.unresolvedDeps == []


def test_unresolved_dep_id_is_surfaced() -> None:
    service, project = _service()
    neighborhood = service.get_neighborhood(project, "T-131")
    # The dangling id is unresolved; the seeded one still resolves to a dep.
    assert _ids(neighborhood.directDeps) == ["T-125"]
    assert neighborhood.unresolvedDeps == ["T-999-missing"]


def test_no_deps_ticket_has_empty_direct_deps_but_keeps_dependents() -> None:
    service, project = _service()
    neighborhood = service.get_neighborhood(project, "T-100")
    assert neighborhood.directDeps == []
    assert neighborhood.unresolvedDeps == []
    assert _ids(neighborhood.directDependents) == ["T-118", "T-125"]


def test_unknown_id_raises_ticket_not_found() -> None:
    service, project = _service()
    with pytest.raises(TicketNotFound) as excinfo:
        service.get_neighborhood(project, "T-777")
    assert excinfo.value.code == "ticket_not_found"
    assert excinfo.value.status == 404
