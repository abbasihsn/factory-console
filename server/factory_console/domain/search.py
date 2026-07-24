"""Full-text search domain model — a single ranked ticket hit.

A :class:`SearchHit` pairs a ticket's list-projection (:class:`TicketSummary`)
with the relevance ``score`` and the ``matchedFields`` that earned it, as
produced by ``file_adapter/search.py``'s ``rank_tickets`` and returned by
``FileAdapter.search_tickets``. It is imported by full path from its consumers
and deliberately NOT re-exported from ``domain/__init__`` so that aggregation
file stays collision-free across the parallel v1 tickets.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from factory_console.domain.ticket import TicketSummary


class SearchHit(BaseModel):
    """One ranked full-text search result: a ticket summary, its score, and hits.

    ``score`` is the summed per-field weight the ranking accrued for this ticket
    (higher is more relevant); ``matchedFields`` names the fields any query token
    hit, in a stable field order (``id``, ``title``, ``provides``,
    ``bodyMarkdown``). Frozen and ``extra='forbid'`` like the other domain models.

    The ``ScoredTicket`` → ``SearchHit`` re-key both adapters share lives in
    ``file_adapter/search.py``'s ``to_search_hits`` — next to its ``ScoredTicket``
    input, importing this model downward (the allowed ``file_adapter → domain``
    direction) — so ``domain`` never has to depend on a ``file_adapter`` type.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket: TicketSummary
    score: float
    matchedFields: list[str] = Field(default_factory=list)
