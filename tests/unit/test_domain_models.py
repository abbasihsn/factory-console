"""Unit tests for the Pydantic v2 domain models.

Cover ticket-id validation (the path-traversal defense), ``model_dump()``
round-trips, ``RunState`` value stability, and frozen immutability. Pure
in-memory models — no I/O.
"""

from datetime import datetime
from pathlib import Path

import pytest
from factory_console.domain import (
    TICKET_ID_PATTERN,
    DepNeighborhood,
    Project,
    Roadmap,
    RunState,
    Ticket,
    TicketSummary,
)
from pydantic import ValidationError


def _make_ticket(ticket_id: str = "CAD-140") -> Ticket:
    return Ticket(
        id=ticket_id,
        title="Sanitize the payload",
        status="todo",
        track="file-adapter",
        milestone="MVP",
        dependsOn=["T02"],
        provides=["models"],
        files=["server/factory_console/domain/ticket.py"],
        filePath=Path("docs/planning/tickets/CAD-140.md"),
        bodyMarkdown="# Body",
        bodyHtml="<h1>Body</h1>",
        raw={"id": ticket_id, "schemaVersion": 1, "nested": {"unknown": True}},
    )


def _make_summary(ticket_id: str = "T07") -> TicketSummary:
    return TicketSummary(
        id=ticket_id,
        title="Domain models",
        status="ready",
        track="file-adapter",
        milestone="MVP",
        runState=RunState.ready,
        depCount=1,
        dependentCount=2,
    )


def test_pattern_is_the_expected_regex() -> None:
    assert TICKET_ID_PATTERN == r"^[A-Za-z0-9_.-]+$"


@pytest.mark.parametrize("ticket_id", ["CAD-140", "T07", "a_b.c-1", "T02"])
def test_valid_ticket_id_accepted(ticket_id: str) -> None:
    assert _make_ticket(ticket_id).id == ticket_id
    assert _make_summary(ticket_id).id == ticket_id


@pytest.mark.parametrize("ticket_id", ["a/b", "a b", "", "foo/bar", "\ttab"])
def test_invalid_ticket_id_rejected(ticket_id: str) -> None:
    with pytest.raises(ValidationError):
        _make_ticket(ticket_id)
    with pytest.raises(ValidationError):
        _make_summary(ticket_id)


def test_ticket_model_dump_round_trip() -> None:
    ticket = _make_ticket()
    assert Ticket(**ticket.model_dump()) == ticket


def test_ticket_summary_model_dump_round_trip() -> None:
    summary = _make_summary()
    assert TicketSummary(**summary.model_dump()) == summary


def test_ticket_raw_passes_through_arbitrary_dict() -> None:
    ticket = _make_ticket()
    assert ticket.raw["schemaVersion"] == 1
    assert ticket.raw["nested"] == {"unknown": True}


def test_ticket_defaults_for_optional_and_list_fields() -> None:
    ticket = Ticket(
        id="T99",
        title="Minimal",
        status="todo",
        filePath=Path("docs/planning/tickets/T99.md"),
        bodyMarkdown="",
        bodyHtml="",
    )
    assert ticket.track is None
    assert ticket.milestone is None
    assert ticket.dependsOn == []
    assert ticket.provides == []
    assert ticket.files == []
    assert ticket.raw == {}


def test_ticket_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Ticket(
            id="T99",
            title="Minimal",
            status="todo",
            filePath=Path("docs/planning/tickets/T99.md"),
            bodyMarkdown="",
            bodyHtml="",
            bogusExtraField=True,
        )


def test_run_state_values_are_stable() -> None:
    assert RunState.todo.value == "todo"
    assert RunState.in_flight.value == "in_flight"
    assert RunState.ready.value == "ready"
    assert RunState.merged.value == "merged"
    assert RunState.unknown.value == "unknown"


def test_run_state_member_set_is_exact() -> None:
    assert {member.name for member in RunState} == {
        "todo",
        "in_flight",
        "ready",
        "merged",
        "unknown",
    }


def test_frozen_ticket_rejects_mutation() -> None:
    ticket = _make_ticket()
    with pytest.raises(ValidationError):
        ticket.title = "reassigned"


def test_frozen_project_rejects_mutation() -> None:
    project = Project(
        rootPath=Path("/tmp/proj"),
        ticketsManifestPath=Path("/tmp/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/tmp/proj/docs/planning/tickets"),
        roadmapPath=None,
        runStateDir=None,
        discoveredAt=datetime(2026, 7, 19, 12, 0, 0),
    )
    with pytest.raises(ValidationError):
        project.rootPath = Path("/tmp/other")


def test_dep_neighborhood_round_trip() -> None:
    neighborhood = DepNeighborhood(
        ticket=_make_summary("T07"),
        directDeps=[_make_summary("T02")],
        directDependents=[_make_summary("T10")],
        unresolvedDeps=["T99"],
    )
    assert DepNeighborhood(**neighborhood.model_dump()) == neighborhood


def test_roadmap_round_trip() -> None:
    roadmap = Roadmap(
        path=Path("docs/planning/ROADMAP.md"),
        bodyMarkdown="# Roadmap",
        bodyHtml="<h1>Roadmap</h1>",
    )
    assert Roadmap(**roadmap.model_dump()) == roadmap
