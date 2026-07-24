"""Full-text search domain model — a single ranked ticket hit.

A :class:`SearchHit` pairs a ticket's list-projection (:class:`TicketSummary`)
with the relevance ``score`` and the ``matchedFields`` that earned it, as
produced by ``file_adapter/search.py``'s ``rank_tickets`` and returned by
``FileAdapter.search_tickets``. It is imported by full path from its consumers
and deliberately NOT re-exported from ``domain/__init__`` so that aggregation
file stays collision-free across the parallel v1 tickets.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from factory_console.domain.ticket import TicketSummary


class SearchHit(BaseModel):
    """One ranked full-text search result: a ticket summary, its score, and hits.

    ``score`` is the summed per-field weight the ranking accrued for this ticket
    (higher is more relevant); ``matchedFields`` names the fields any query token
    hit, in a stable field order (``id``, ``title``, ``provides``,
    ``bodyMarkdown``). Frozen and ``extra='forbid'`` like the other domain models.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket: TicketSummary
    score: float
    matchedFields: list[str] = Field(default_factory=list)


class _Scored(Protocol):
    """Structural shape of a ranked ticket (``file_adapter.search.ScoredTicket``).

    Declared here purely so :func:`to_search_hits` can be typed WITHOUT importing
    the concrete ``ScoredTicket`` — that would invert the layering (``domain``
    must never depend on ``file_adapter``). Any object exposing an ``id``, a
    ``score``, and ``matched_fields`` satisfies it.
    """

    @property
    def id(self) -> str: ...

    @property
    def score(self) -> float: ...

    @property
    def matched_fields(self) -> list[str]: ...


def to_search_hits(
    scored: Iterable[_Scored],
    summary_by_id: Mapping[str, TicketSummary],
    limit: int | None = None,
) -> list[SearchHit]:
    """Re-key ranked tickets to :class:`SearchHit`\\ s, then apply ``limit``.

    The single construction point both ``FileAdapter`` implementations share:
    each ranked ticket becomes a ``SearchHit`` whose ``ticket`` is the projected
    summary looked up by id, bridging the internal ``matched_fields`` (snake) to
    the wire ``matchedFields`` (camel) in one place. ``limit`` truncates to the
    first ``limit`` hits when not ``None``, so the "first N" policy can never
    disagree between the two adapters.
    """
    hits = [
        SearchHit(
            ticket=summary_by_id[item.id], score=item.score, matchedFields=item.matched_fields
        )
        for item in scored
    ]
    return hits if limit is None else hits[:limit]
