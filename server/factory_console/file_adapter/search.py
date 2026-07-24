"""Relevance ranking, plus the ranked-ticket → :class:`SearchHit` re-key.

:func:`rank_tickets` is the whole scoring algorithm for
``FileAdapter.search_tickets``: it takes an already-materialized list of
:class:`~factory_console.domain.ticket.Ticket` (bodies enriched by the caller)
and a raw query string and returns the matching tickets, most-relevant first, as
lightweight :class:`ScoredTicket` records. It touches no filesystem and depends
only on the stdlib and the :class:`Ticket` model — the adapters own the I/O
(reading ``.md`` bodies, resolving run-state); this scoring half is a pure fold
so it can be unit-tested against hand-built ticket lists.

:func:`to_search_hits` is the single re-key step both adapters share: it maps
each :class:`ScoredTicket` back to a domain
:class:`~factory_console.domain.search.SearchHit` (looking the summary up by id)
and applies ``limit``. It lives here — beside its ``ScoredTicket`` input and
``rank_tickets`` producer — rather than in ``domain/search.py`` because that
would force ``domain`` to depend on this ``file_adapter`` type; the
``file_adapter → domain`` import of :class:`SearchHit` is the allowed direction,
so ``ScoredTicket`` is referenced directly (no shadow Protocol to drift).

Scoring: the query is lowercased and split on whitespace into tokens (a blank or
whitespace-only query yields no tokens and therefore ``[]``). For each token,
each searched field that CONTAINS the token (case-insensitive substring) adds
that field's weight to the ticket's score once and records the field name.
``matchedFields`` is the set of fields ANY token hit, emitted in the fixed
:data:`_FIELD_ORDER` order (never Python set-iteration order, which is not
stable across runs). Tickets scoring zero are dropped; the rest are sorted by
score descending. The sort is stable and keyed on score ALONE, so equal-score
tickets keep their input order (never re-ordered by id).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from factory_console.domain.search import SearchHit
from factory_console.domain.ticket import Ticket, TicketSummary

# Field weights. The strict ordering the ticket mandates is id == title >
# provides > bodyMarkdown: a hit in a ticket's id or title is the strongest
# signal (and the two are equally strong), a `provides` hit is weaker, and a
# body hit is the weakest. The concrete numbers only have to preserve that
# ordering; these do.
_WEIGHT_ID = 3.0
_WEIGHT_TITLE = 3.0
_WEIGHT_PROVIDES = 2.0
_WEIGHT_BODY = 1.0

# The fixed, documented order matchedFields is emitted in — decoupled from the
# order tokens happen to hit fields, so the result is deterministic regardless
# of the query. Names match the camelCase Ticket fields (`id`, `title`,
# `provides`, `bodyMarkdown`).
_FIELD_ORDER = ("id", "title", "provides", "bodyMarkdown")


@dataclass(frozen=True)
class ScoredTicket:
    """Internal ranking result for one ticket — id, summed score, matched fields.

    Deliberately not a domain model: it carries only the ticket ``id`` (the
    adapter re-keys it to a :class:`~factory_console.domain.search.SearchHit`
    with the projected summary), the accumulated ``score``, and
    ``matched_fields`` in :data:`_FIELD_ORDER` order.
    """

    id: str
    score: float
    matched_fields: list[str] = field(default_factory=list)


def _field_texts(ticket: Ticket) -> dict[str, tuple[str, float]]:
    """Map each searched field name to its lowercased text and weight.

    ``provides`` (a ``list[str]``) is flattened to a single lowercased,
    newline-joined blob so a token matching ANY entry counts as a ``provides``
    hit; the other fields are plain lowercased strings.
    """
    return {
        "id": (ticket.id.lower(), _WEIGHT_ID),
        "title": (ticket.title.lower(), _WEIGHT_TITLE),
        "provides": ("\n".join(ticket.provides).lower(), _WEIGHT_PROVIDES),
        "bodyMarkdown": (ticket.bodyMarkdown.lower(), _WEIGHT_BODY),
    }


def rank_tickets(tickets: list[Ticket], query: str) -> list[ScoredTicket]:
    """Rank ``tickets`` against ``query``, most-relevant first.

    Lowercases and whitespace-splits ``query`` into tokens; a blank or
    whitespace-only query has no tokens and returns ``[]``. For each ticket, each
    token that is a substring of a field's lowercased text adds that field's
    weight ONCE and records the field name; ``matchedFields`` is the set of
    fields any token hit, in :data:`_FIELD_ORDER`. Zero-score tickets are
    dropped. The result is sorted by score descending via a STABLE sort keyed on
    score alone, so tickets with equal scores retain their input order.
    """
    tokens = query.lower().split()
    if not tokens:
        return []

    scored: list[ScoredTicket] = []
    for ticket in tickets:
        texts = _field_texts(ticket)
        score = 0.0
        hit_fields: set[str] = set()
        for token in tokens:
            for name, (text, weight) in texts.items():
                if token in text:
                    score += weight
                    hit_fields.add(name)
        if score == 0.0:
            continue
        matched = [name for name in _FIELD_ORDER if name in hit_fields]
        scored.append(ScoredTicket(id=ticket.id, score=score, matched_fields=matched))

    # Stable sort on score alone: sorted() preserves input order for ties, so we
    # must NOT add id as a secondary key — equal-score tickets keep their
    # incoming (manifest) order.
    scored.sort(key=lambda hit: hit.score, reverse=True)
    return scored


def to_search_hits(
    scored: Iterable[ScoredTicket],
    summary_by_id: Mapping[str, TicketSummary],
    limit: int | None = None,
) -> list[SearchHit]:
    """Re-key ranked tickets to :class:`SearchHit`\\ s, then apply ``limit``.

    The single construction point both ``FileAdapter`` implementations share:
    each :class:`ScoredTicket` becomes a ``SearchHit`` whose ``ticket`` is the
    projected summary looked up by id, bridging the internal ``matched_fields``
    (snake) to the wire ``matchedFields`` (camel) in one place. ``limit``
    truncates to the first ``limit`` hits when not ``None``, so the "first N"
    policy can never disagree between the two adapters.
    """
    hits = [
        SearchHit(
            ticket=summary_by_id[item.id], score=item.score, matchedFields=item.matched_fields
        )
        for item in scored
    ]
    return hits if limit is None else hits[:limit]
