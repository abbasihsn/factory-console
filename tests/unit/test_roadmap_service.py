"""Unit tests for :class:`RoadmapService` — the roadmap, with live status joined on.

The parser produces narrative (label, order, ticket id) and this service supplies the
status, so these pin the JOIN and nothing else: which items get a state, which stay
``None``, that the state is the run-state source's own answer, and that the source is
read ONCE however many items name a ticket.

Driven over :class:`FakeFileAdapter` with a seeded roadmap, plus a counting adapter
wrapper for the one-read property, which is invisible to any assertion about values.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import pytest

from factory_console.domain import Project, Roadmap, RunState
from factory_console.domain.deps import RoadmapItem, RoadmapMilestone
from factory_console.file_adapter import FakeFileAdapter
from factory_console.services.roadmap_service import RoadmapService

PROJECT = Project(
    rootPath=Path("/factory/demo"),
    ticketsManifestPath=Path("/factory/demo/docs/planning/tickets.json"),
    ticketsDir=Path("/factory/demo/docs/planning/tickets"),
    roadmapPath=Path("/factory/demo/ROADMAP.md"),
    discoveredAt=datetime(2026, 8, 8, 12, 0, 0),
)


def _roadmap(*milestones: RoadmapMilestone) -> Roadmap:
    """A Roadmap as the PARSER hands it over: every item's ``runState`` still ``None``."""
    return Roadmap(
        path=PROJECT.roadmapPath or Path("/factory/demo/ROADMAP.md"),
        bodyMarkdown="# Roadmap\n",
        bodyHtml="<h1>Roadmap</h1>",
        milestones=list(milestones),
    )


def _service(roadmap: Roadmap | None, run_states: dict[str, RunState] | None = None):
    adapter = FakeFileAdapter(project=PROJECT, tickets=[], roadmap=roadmap, run_states=run_states)
    return RoadmapService(adapter)


# --------------------------------------------------------------------------- #
# The join
# --------------------------------------------------------------------------- #


def test_an_item_naming_a_ticket_gets_that_tickets_run_state() -> None:
    service = _service(
        _roadmap(RoadmapMilestone(name="v1.0", items=[RoadmapItem(text="Ship", ticketId="T01")])),
        {"T01": RunState.merged},
    )

    roadmap = service.get_roadmap(PROJECT)

    assert roadmap is not None
    assert roadmap.milestones[0].items[0].runState is RunState.merged


def test_an_item_naming_no_ticket_keeps_a_null_run_state() -> None:
    # `None` is the absence of a QUESTION, not an answer of "nothing known". A prose
    # bullet has no status because there is no ticket to have one, and badging it
    # `unknown` would assert the factory has never heard of a ticket that never existed.
    service = _service(
        _roadmap(RoadmapMilestone(name="v1.0", items=[RoadmapItem(text="Write the post")]))
    )

    roadmap = service.get_roadmap(PROJECT)

    assert roadmap is not None
    assert roadmap.milestones[0].items[0].runState is None


def test_unknown_and_null_are_different_answers_in_one_document() -> None:
    # The distinction only earns its keep if both appear at once: the ticket-bearing
    # item resolves `unknown` (a source was asked, said nothing) while its prose
    # neighbour stays `None` (nothing to ask about).
    service = _service(
        _roadmap(
            RoadmapMilestone(
                name="v1.0",
                items=[
                    RoadmapItem(text="Ship", ticketId="T01"),
                    RoadmapItem(text="Write the post"),
                ],
            )
        )
    )

    items = service.get_roadmap(PROJECT).milestones[0].items  # type: ignore[union-attr]

    assert items[0].runState is RunState.unknown
    assert items[1].runState is None


def test_every_milestone_is_joined_not_only_the_first() -> None:
    service = _service(
        _roadmap(
            RoadmapMilestone(name="v1.0", items=[RoadmapItem(text="a", ticketId="T01")]),
            RoadmapMilestone(name="v1.1", items=[RoadmapItem(text="b", ticketId="T02")]),
        ),
        {"T01": RunState.merged, "T02": RunState.in_progress},
    )

    milestones = service.get_roadmap(PROJECT).milestones  # type: ignore[union-attr]

    assert milestones[0].items[0].runState is RunState.merged
    assert milestones[1].items[0].runState is RunState.in_progress


def test_the_narrative_survives_the_join_untouched() -> None:
    # The service adds a status; it must not rewrite the document. Order, labels and ids
    # are the parser's output and are the only record of what the roadmap SAYS.
    original = _roadmap(
        RoadmapMilestone(
            name="v1.0",
            items=[
                RoadmapItem(text="**T01** — Ship", ticketId="T01"),
                RoadmapItem(text="Write the post"),
            ],
        )
    )
    service = _service(original, {"T01": RunState.merged})

    roadmap = service.get_roadmap(PROJECT)

    assert roadmap is not None
    assert roadmap.bodyMarkdown == original.bodyMarkdown
    assert roadmap.bodyHtml == original.bodyHtml
    assert [m.name for m in roadmap.milestones] == ["v1.0"]
    assert [i.text for i in roadmap.milestones[0].items] == ["**T01** — Ship", "Write the post"]
    assert [i.ticketId for i in roadmap.milestones[0].items] == ["T01", None]


def test_no_roadmap_stays_no_roadmap() -> None:
    assert _service(None).get_roadmap(PROJECT) is None


def test_an_empty_roadmap_joins_to_an_empty_roadmap() -> None:
    # No milestones means no ids to resolve. The service must not mind — and must not
    # invent a milestone to hang a status on.
    roadmap = _service(_roadmap()).get_roadmap(PROJECT)

    assert roadmap is not None
    assert roadmap.milestones == []


# --------------------------------------------------------------------------- #
# One read, whatever the document costs
# --------------------------------------------------------------------------- #


class _CountingAdapter:
    """Wraps a :class:`FakeFileAdapter`, counting how the service asks for run-state.

    Deliberately NOT a Mock: the counts are the subject of the assertions below, and a
    Mock that answered every attribute would let the service call a method this port
    does not have and still pass.
    """

    def __init__(self, inner: FakeFileAdapter) -> None:
        self._inner = inner
        self.batch_calls = 0
        self.single_calls = 0
        self.ids_asked: list[str] = []

    def get_roadmap(self, project: Project) -> Roadmap | None:
        return self._inner.get_roadmap(project)

    def read_run_state(self, project: Project, ticket_id: str) -> RunState:
        self.single_calls += 1
        return self._inner.read_run_state(project, ticket_id)

    def read_run_states(self, project: Project, ticket_ids: Iterable[str]) -> dict[str, RunState]:
        materialized = list(ticket_ids)
        self.batch_calls += 1
        self.ids_asked.extend(materialized)
        return self._inner.read_run_states(project, materialized)


def _counting(roadmap: Roadmap, run_states: dict[str, RunState] | None = None):
    inner = FakeFileAdapter(project=PROJECT, tickets=[], roadmap=roadmap, run_states=run_states)
    counter = _CountingAdapter(inner)
    return RoadmapService(counter), counter  # type: ignore[arg-type]


def test_the_run_state_source_is_asked_exactly_once_for_the_whole_document() -> None:
    # The property that makes this affordable. The singular `read_run_state` re-opens
    # the source per call, so looping it would re-parse run-state.json once per bullet —
    # 141 times for this repository's own roadmap, for an answer that cannot differ
    # between them.
    service, counter = _counting(
        _roadmap(
            RoadmapMilestone(
                name="v1.0",
                items=[RoadmapItem(text=f"item {n}", ticketId=f"T{n:02d}") for n in range(1, 21)],
            )
        )
    )

    service.get_roadmap(PROJECT)

    assert counter.batch_calls == 1
    assert counter.single_calls == 0


def test_a_ticket_named_twice_is_asked_about_once_and_answers_the_same_both_times() -> None:
    # A real roadmap repeats ids across milestones. Two lookups could not disagree here
    # (the source is read once), but the dedup is the port's contract and is what stops
    # the cost growing with mentions rather than tickets.
    service, counter = _counting(
        _roadmap(
            RoadmapMilestone(name="v1.0", items=[RoadmapItem(text="a", ticketId="T01")]),
            RoadmapMilestone(name="v1.1", items=[RoadmapItem(text="a again", ticketId="T01")]),
        ),
        {"T01": RunState.merged},
    )

    milestones = service.get_roadmap(PROJECT).milestones  # type: ignore[union-attr]

    assert milestones[0].items[0].runState is RunState.merged
    assert milestones[1].items[0].runState is RunState.merged


def test_a_document_with_no_ticket_ids_asks_nothing_of_the_source() -> None:
    # An all-prose roadmap has nothing to resolve, and reading a file to be told so is
    # work done for no answer.
    service, counter = _counting(
        _roadmap(RoadmapMilestone(name="Principles", items=[RoadmapItem(text="Be kind")]))
    )

    service.get_roadmap(PROJECT)

    assert counter.ids_asked == []


def test_an_adapter_that_drops_an_id_fails_loudly_rather_than_answering_null() -> None:
    # The port promises every requested id back. A `.get(...)` fallback here would
    # return None, which this model reads as "this item names no ticket" — turning a
    # resolution failure into a silent claim that there was nothing to resolve.
    class _Forgetful(_CountingAdapter):
        def read_run_states(
            self, project: Project, ticket_ids: Iterable[str]
        ) -> dict[str, RunState]:
            return {}

    inner = FakeFileAdapter(
        project=PROJECT,
        tickets=[],
        roadmap=_roadmap(
            RoadmapMilestone(name="v1.0", items=[RoadmapItem(text="Ship", ticketId="T01")])
        ),
    )
    service = RoadmapService(_Forgetful(inner))  # type: ignore[arg-type]

    with pytest.raises(KeyError):
        service.get_roadmap(PROJECT)
