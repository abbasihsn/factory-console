"""Unit tests for :class:`SearchService` over the in-memory :class:`FakeFileAdapter`.

Pin the two behaviors the HTTP handler delegates to: query normalization (strip
surrounding whitespace; a blank or whitespace-only query short-circuits to ``[]``
WITHOUT consulting the adapter) and ``limit`` passthrough to the adapter's
``search_tickets``. Deterministic and I/O-free: the fake ranks the seeded
in-memory tickets — whose distinctive ``bodyMarkdown`` drives the matching — with
the same pure ranker the real adapter uses.
"""

from datetime import datetime
from pathlib import Path

from factory_console.domain import Project, Ticket
from factory_console.file_adapter import FakeFileAdapter
from factory_console.services.search_service import SearchService


def _make_project() -> Project:
    return Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        roadmapPath=Path("/proj/ROADMAP.md"),
        runStateDir=Path("/proj/.factory/run-state"),
        discoveredAt=datetime(2026, 7, 21, 12, 0, 0),
    )


def _make_ticket(ticket_id: str, *, title: str, body: str) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=title,
        status="todo",
        track="backend",
        milestone="MVP",
        filePath=Path(f"/proj/docs/planning/tickets/{ticket_id}.md"),
        bodyMarkdown=body,
        bodyHtml=f"<p>{body}</p>",
        raw={"id": ticket_id},
    )


# Three tickets whose bodies share the distinctive word "streak" so a single
# query can match more than one (proving limit truncation), plus one unrelated.
def _tickets() -> list[Ticket]:
    return [
        _make_ticket("T-100", title="Alpha", body="The streak computation runs nightly."),
        _make_ticket("T-118", title="Beta", body="A streak resets when a day is missed."),
        _make_ticket("T-125", title="Gamma", body="Longest streak is shown on the profile."),
        _make_ticket("T-140", title="Delta", body="Weekly digest email delivery."),
    ]


def _service() -> tuple[SearchService, Project]:
    project = _make_project()
    fake = FakeFileAdapter(project=project, tickets=_tickets())
    return SearchService(fake), project


def _ids(hits: list) -> list[str]:
    return [hit.ticket.id for hit in hits]


# --------------------------------------------------------------------------- #
# Query normalization (strip; blank/whitespace-only -> [])
# --------------------------------------------------------------------------- #


def test_blank_query_returns_empty_without_matching() -> None:
    service, project = _service()
    assert service.search(project, "", limit=50) == []


def test_whitespace_only_query_returns_empty_without_matching() -> None:
    service, project = _service()
    assert service.search(project, "   ", limit=50) == []


def test_surrounding_whitespace_is_stripped_and_matches_same_as_bare_term() -> None:
    service, project = _service()
    padded = service.search(project, "  streak  ", limit=50)
    bare = service.search(project, "streak", limit=50)
    assert _ids(padded) == _ids(bare)
    # The strip passthrough actually reaches the body-matched tickets.
    assert set(_ids(padded)) == {"T-100", "T-118", "T-125"}


# --------------------------------------------------------------------------- #
# limit passthrough
# --------------------------------------------------------------------------- #


def test_limit_truncates_to_first_n_hits() -> None:
    service, project = _service()
    # Three tickets match "streak"; limit=1 truncates to one, a larger limit
    # returns all three.
    assert len(service.search(project, "streak", limit=1)) == 1
    assert len(service.search(project, "streak", limit=50)) == 3
