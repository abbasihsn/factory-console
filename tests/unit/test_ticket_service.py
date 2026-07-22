"""Unit tests for :class:`TicketService` over the in-memory :class:`FakeFileAdapter`.

Pin the service's two behaviors the HTTP handlers delegate to: filtering the
already-run-state-resolved summaries (status/track/milestone equality plus a
case-insensitive ``q`` substring over id AND title, combined with AND, input
order preserved) and resolving ticket detail with run-state joined in — raising
:class:`TicketNotFound` for an unseeded id. Deterministic and I/O-free: the fake
answers every call from seeded in-memory data.
"""

from datetime import datetime
from pathlib import Path

import pytest

from factory_console.domain import Project, RunState, Ticket
from factory_console.file_adapter import FakeFileAdapter
from factory_console.services.ticket_service import TicketNotFound, TicketService


def _make_project() -> Project:
    return Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        roadmapPath=Path("/proj/ROADMAP.md"),
        runStateDir=Path("/proj/.factory/run-state"),
        discoveredAt=datetime(2026, 7, 21, 12, 0, 0),
    )


def _make_ticket(
    ticket_id: str,
    *,
    status: str,
    track: str | None,
    milestone: str | None,
    title: str,
) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=title,
        status=status,
        track=track,
        milestone=milestone,
        filePath=Path(f"/proj/docs/planning/tickets/{ticket_id}.md"),
        bodyMarkdown=f"# {title}",
        bodyHtml=f"<h1>{title}</h1>",
        raw={"id": ticket_id},
    )


# Four tickets spanning distinct status/track/milestone/title so every filter
# selects a meaningful subset; run-states seeded for three of them (T-140 unseeded
# so it resolves to unknown).
def _tickets() -> list[Ticket]:
    return [
        _make_ticket(
            "T-100",
            status="todo",
            track="backend",
            milestone="MVP",
            title="Streak computation service",
        ),
        _make_ticket(
            "T-118",
            status="todo",
            track="frontend",
            milestone="MVP",
            title="Habit heatmap calendar",
        ),
        _make_ticket(
            "T-125",
            status="in_review",
            track="backend",
            milestone="v1",
            title="Weekly digest email",
        ),
        _make_ticket(
            "T-140",
            status="done",
            track="api",
            milestone="v2",
            title="Daily check-in endpoints",
        ),
    ]


_RUN_STATES = {
    "T-100": RunState.ready,
    "T-118": RunState.in_flight,
    "T-125": RunState.merged,
}


def _service() -> tuple[TicketService, Project]:
    project = _make_project()
    fake = FakeFileAdapter(project=project, tickets=_tickets(), run_states=_RUN_STATES)
    return TicketService(fake), project


def _ids(tickets: list) -> list[str]:
    return [ticket.id for ticket in tickets]


# --------------------------------------------------------------------------- #
# list_tickets — filters
# --------------------------------------------------------------------------- #


def test_no_filter_returns_every_ticket_in_input_order() -> None:
    service, project = _service()
    result = service.list_tickets(project, status=None, track=None, milestone=None, q=None)
    assert _ids(result) == ["T-100", "T-118", "T-125", "T-140"]


def test_status_only_filter_selects_matching_status() -> None:
    service, project = _service()
    result = service.list_tickets(project, status="todo", track=None, milestone=None, q=None)
    assert _ids(result) == ["T-100", "T-118"]


def test_track_only_filter_selects_matching_track() -> None:
    service, project = _service()
    result = service.list_tickets(project, status=None, track="backend", milestone=None, q=None)
    assert _ids(result) == ["T-100", "T-125"]


def test_milestone_only_filter_selects_matching_milestone() -> None:
    service, project = _service()
    result = service.list_tickets(project, status=None, track=None, milestone="MVP", q=None)
    assert _ids(result) == ["T-100", "T-118"]


def test_combined_status_and_track_filters_and_together() -> None:
    service, project = _service()
    result = service.list_tickets(project, status="todo", track="backend", milestone=None, q=None)
    assert _ids(result) == ["T-100"]


# --------------------------------------------------------------------------- #
# list_tickets — q substring (case-insensitive over id AND title)
# --------------------------------------------------------------------------- #


def test_q_matches_title_case_insensitively() -> None:
    service, project = _service()
    # Lowercase needle against a title that starts with "Streak".
    result = service.list_tickets(project, status=None, track=None, milestone=None, q="streak")
    assert _ids(result) == ["T-100"]


def test_q_matches_id_case_insensitively() -> None:
    service, project = _service()
    # Lowercase needle against the uppercase id "T-125" (no title contains it).
    result = service.list_tickets(project, status=None, track=None, milestone=None, q="t-125")
    assert _ids(result) == ["T-125"]


def test_q_with_no_match_returns_empty_list() -> None:
    service, project = _service()
    result = service.list_tickets(project, status=None, track=None, milestone=None, q="nonexistent")
    assert result == []


# --------------------------------------------------------------------------- #
# list_tickets — run-state carried on summaries
# --------------------------------------------------------------------------- #


def test_list_summaries_carry_the_seeded_run_state() -> None:
    service, project = _service()
    result = service.list_tickets(project, status=None, track=None, milestone=None, q=None)
    by_id = {summary.id: summary.runState for summary in result}
    assert by_id["T-100"] is RunState.ready
    assert by_id["T-118"] is RunState.in_flight
    # Unseeded ticket -> unknown.
    assert by_id["T-140"] is RunState.unknown


# --------------------------------------------------------------------------- #
# get_ticket — run-state join + not-found
# --------------------------------------------------------------------------- #


def test_get_ticket_joins_seeded_run_state_on_detail_path() -> None:
    service, project = _service()
    # T-100's seeded run-state is `ready` (not `unknown`), so the join is proven.
    ticket = service.get_ticket(project, "T-100")
    assert ticket.id == "T-100"
    assert ticket.runState is RunState.ready


def test_get_ticket_leaves_unseeded_run_state_unknown() -> None:
    service, project = _service()
    ticket = service.get_ticket(project, "T-140")
    assert ticket.runState is RunState.unknown


def test_get_ticket_raises_ticket_not_found_for_unseeded_id() -> None:
    service, project = _service()
    with pytest.raises(TicketNotFound) as excinfo:
        service.get_ticket(project, "T-999")
    assert excinfo.value.code == "ticket_not_found"
    assert excinfo.value.status == 404
