"""Unit tests for the pure ``rank_tickets`` relevance ranking.

These pin the whole scoring contract ``FileAdapter.search_tickets`` delegates to,
against hand-built :class:`Ticket` lists — no filesystem, pydantic + stdlib only:
the field-weight ordering (id/title beat provides beat body), multi-token score
accumulation, ``matchedFields`` correctness and stable order, blank/whitespace
query returning ``[]``, zero-score tickets dropped, and stable ordering on score
ties. A couple of :class:`SearchHit` model tests pin frozen / ``extra='forbid'``.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from factory_console.domain.search import SearchHit
from factory_console.domain.ticket import RunState, Ticket, TicketSummary
from factory_console.file_adapter.search import (
    _WEIGHT_BODY,
    _WEIGHT_ID,
    _WEIGHT_PROVIDES,
    _WEIGHT_TITLE,
    ScoredTicket,
    rank_tickets,
)


def _make_ticket(
    ticket_id: str,
    *,
    title: str = "",
    provides: list[str] | None = None,
    body: str = "",
) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=title,
        status="open",
        provides=provides or [],
        filePath=Path("x.md"),
        bodyMarkdown=body,
        bodyHtml="",
        raw={},
    )


# --------------------------------------------------------------------------- #
# weight ordering: id == title > provides > bodyMarkdown
# --------------------------------------------------------------------------- #


def test_weights_are_strictly_ordered_id_eq_title_gt_provides_gt_body() -> None:
    assert _WEIGHT_ID == _WEIGHT_TITLE > _WEIGHT_PROVIDES > _WEIGHT_BODY


def test_id_and_title_hits_outrank_provides_and_body_hits() -> None:
    tickets = [
        _make_ticket("BODY-1", title="unrelated", body="the widget hums"),
        _make_ticket("PROV-1", title="unrelated", provides=["a widget provider"]),
        _make_ticket("widget-id", title="unrelated"),
        _make_ticket("TITLE-1", title="a widget headline"),
    ]
    ranked = rank_tickets(tickets, "widget")
    # id-hit and title-hit tickets (weight 3.0) rank above provides (2.0) above body (1.0).
    ids = [hit.id for hit in ranked]
    assert ids[0:2] == ["widget-id", "TITLE-1"] or ids[0:2] == ["TITLE-1", "widget-id"]
    assert ids[2] == "PROV-1"
    assert ids[3] == "BODY-1"
    scores = {hit.id: hit.score for hit in ranked}
    assert scores["widget-id"] == _WEIGHT_ID
    assert scores["TITLE-1"] == _WEIGHT_TITLE
    assert scores["PROV-1"] == _WEIGHT_PROVIDES
    assert scores["BODY-1"] == _WEIGHT_BODY


# --------------------------------------------------------------------------- #
# multi-token accumulation
# --------------------------------------------------------------------------- #


def test_multiple_tokens_accumulate_score_across_fields() -> None:
    ticket = _make_ticket("alpha", title="beta headline", body="gamma paragraph")
    # 'alpha' hits id (3.0), 'beta' hits title (3.0), 'gamma' hits body (1.0).
    ranked = rank_tickets([ticket], "alpha beta gamma")
    assert len(ranked) == 1
    assert ranked[0].score == _WEIGHT_ID + _WEIGHT_TITLE + _WEIGHT_BODY
    assert ranked[0].matched_fields == ["id", "title", "bodyMarkdown"]


def test_partial_match_is_or_not_and_and_ranks_full_match_first() -> None:
    # OR semantics: a ticket matching only ONE of two tokens is still returned
    # (only zero-score tickets drop). The ticket hitting BOTH tokens outscores it
    # and ranks first. Pins the partial-match contract against a regression to AND.
    both = _make_ticket("streak-heatmap", title="unrelated")  # id hits both tokens
    one = _make_ticket("streak-only", title="unrelated")  # id hits only 'streak'
    ranked = rank_tickets([one, both], "streak heatmap")
    assert [hit.id for hit in ranked] == ["streak-heatmap", "streak-only"]
    assert ranked[0].score == _WEIGHT_ID * 2
    assert ranked[1].score == _WEIGHT_ID


def test_query_is_lowercased_so_matching_is_case_insensitive() -> None:
    ticket = _make_ticket("CAD-100", title="Streak Service")
    ranked = rank_tickets([ticket], "STREAK")
    assert len(ranked) == 1
    assert ranked[0].matched_fields == ["title"]


# --------------------------------------------------------------------------- #
# matchedFields correctness + stable order
# --------------------------------------------------------------------------- #


def test_matched_fields_are_in_fixed_order_not_hit_order() -> None:
    # 'zzz' hits only the body, 'aaa' hits only the id; despite the tokens hitting
    # body-before-id, matchedFields is emitted in the fixed id,title,provides,body order.
    ticket = _make_ticket("aaa", body="zzz")
    ranked = rank_tickets([ticket], "zzz aaa")
    assert ranked[0].matched_fields == ["id", "bodyMarkdown"]


def test_provides_matches_any_entry_and_names_the_field_provides() -> None:
    ticket = _make_ticket("t1", provides=["first thing", "second widget thing"])
    ranked = rank_tickets([ticket], "widget")
    assert ranked[0].matched_fields == ["provides"]
    assert ranked[0].score == _WEIGHT_PROVIDES


def test_a_field_is_recorded_once_even_when_several_tokens_hit_it() -> None:
    # both tokens are substrings of the title; the title weight is added per token
    # (accumulates) but the field name appears once.
    ticket = _make_ticket("t1", title="streak and heatmap")
    ranked = rank_tickets([ticket], "streak heatmap")
    assert ranked[0].matched_fields == ["title"]
    assert ranked[0].score == _WEIGHT_TITLE * 2


# --------------------------------------------------------------------------- #
# blank query, zero-score drop
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("query", ["", "   ", "\t\n  "])
def test_blank_or_whitespace_query_returns_empty(query: str) -> None:
    tickets = [_make_ticket("t1", title="anything")]
    assert rank_tickets(tickets, query) == []


def test_zero_score_tickets_are_dropped() -> None:
    tickets = [
        _make_ticket("hit", title="widget"),
        _make_ticket("miss", title="nothing relevant"),
    ]
    ranked = rank_tickets(tickets, "widget")
    assert [hit.id for hit in ranked] == ["hit"]


# --------------------------------------------------------------------------- #
# stable ordering on ties
# --------------------------------------------------------------------------- #


def test_equal_scores_keep_input_order() -> None:
    # Two tickets each match title only (same score); the sort must be stable on
    # input order — NOT re-ordered by id — so the first-seen ticket stays first.
    tickets = [
        _make_ticket("ZZZ-2", title="shared keyword"),
        _make_ticket("AAA-1", title="shared keyword"),
    ]
    ranked = rank_tickets(tickets, "keyword")
    assert [hit.id for hit in ranked] == ["ZZZ-2", "AAA-1"]
    assert ranked[0].score == ranked[1].score


def test_rank_tickets_returns_scored_ticket_instances() -> None:
    ranked = rank_tickets([_make_ticket("t1", title="widget")], "widget")
    assert isinstance(ranked[0], ScoredTicket)


# --------------------------------------------------------------------------- #
# SearchHit domain model
# --------------------------------------------------------------------------- #


def _summary(ticket_id: str) -> TicketSummary:
    return TicketSummary(
        id=ticket_id,
        title="t",
        status="open",
        runState=RunState.unknown,
        depCount=0,
        dependentCount=0,
    )


def test_search_hit_is_frozen_and_forbids_extra_fields() -> None:
    hit = SearchHit(ticket=_summary("t1"), score=3.0, matchedFields=["id"])
    with pytest.raises(ValidationError):
        hit.score = 9.0  # type: ignore[misc]
    with pytest.raises(ValidationError):
        SearchHit(ticket=_summary("t1"), score=1.0, matchedFields=[], nope=1)  # type: ignore[call-arg]
