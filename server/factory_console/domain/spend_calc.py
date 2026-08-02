"""The pure aggregator: ledger entries in, a :class:`SpendReport` out.

:func:`aggregate` is total and side-effect free — no I/O, no ``Project``, no
paths, no clock. That is what makes the attribution rule testable directly, at the
level it is decided, instead of only through an HTTP round trip; the endpoint in
``api/v1/spend.py`` is then a thin assembly of this result with the two facts only
a reader knows (whether a ledger existed, and which lines it could not parse).

An empty list is a VALID input yielding zeroed totals. It is emphatically not an
error and not an absence: this function cannot tell an empty ledger from a missing
one, so it does not try — see :class:`~factory_console.domain.spend.SourceInfo`.

Three decisions worth reading before changing anything here.

**Costs are summed with** :func:`math.fsum`, **not** ``+=``. Every figure is a
float the factory wrote, and a naive running sum over hundreds of entries
accumulates rounding error in the last places — exactly the places a per-model
breakdown is interesting in. ``fsum`` is exactly rounded over the whole sequence,
so the total depends only on the values, never on the order they were read in.
Rounding happens ONCE, where the response model is built.

**By-model uses ``by_model`` when it is present**, because that map is the
authoritative breakdown: a single lane routinely mixes three models, and the
entry's scalar ``model`` names at most one of them. Only when an entry carries no
breakdown at all does its ``model`` become the bucket, so its cost still appears
somewhere rather than dropping out of the by-model cut. Those two vocabularies can
differ (the factory writes ``"sonnet"`` in ``model`` and ``"claude-sonnet-5"`` in
``by_model``); nothing here maps between them, since a guessed mapping would merge
two figures that were never the same measurement.

**Nothing here reads** ``session_id``. It is excluded from serialisation at the
:class:`~factory_console.domain.ledger.LedgerEntry` level, but this module is the
one place that could copy it into a shape that is not excluded, so it simply never
touches the attribute.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from factory_console.domain.ledger import LedgerEntry, TokenCounts
from factory_console.domain.spend import (
    COST_DECIMAL_PLACES,
    LevelSpend,
    ModelSpend,
    SpendReport,
    SpendTotals,
    TicketSpend,
)


@dataclass
class _Tokens:
    """A mutable token accumulator, summed exactly (these are ints, not money)."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    total: int = 0

    def add(
        self,
        *,
        input: int,
        output: int,
        cache_read: int,
        cache_creation: int,
        total: int,
    ) -> None:
        """Add one contribution's five counts."""
        self.input += input
        self.output += output
        self.cache_read += cache_read
        self.cache_creation += cache_creation
        self.total += total

    def frozen(self) -> TokenCounts:
        """Return the immutable :class:`TokenCounts` for a response model."""
        return TokenCounts(
            input=self.input,
            output=self.output,
            cache_read=self.cache_read,
            cache_creation=self.cache_creation,
            total=self.total,
        )


@dataclass
class _Bucket:
    """One aggregation bucket: the costs to sum, how many entries, which models.

    ``costs`` is a LIST, not a running total, precisely so :func:`math.fsum` can
    round the whole sequence once at the end.
    """

    costs: list[float] = field(default_factory=list)
    entries: int = 0
    models: set[str] = field(default_factory=set)
    tokens: _Tokens = field(default_factory=_Tokens)

    def cost(self) -> float:
        """The bucket's exactly-summed, boundary-rounded dollar figure."""
        return round(math.fsum(self.costs), COST_DECIMAL_PLACES)


def aggregate(entries: list[LedgerEntry]) -> SpendReport:
    """Aggregate ledger ``entries`` into totals plus the by-ticket/model/level cuts.

    Pure and total: the same entries always give the same report, an empty list
    gives zeroed totals rather than an error, and nothing is read or written.

    Attribution follows :data:`~factory_console.domain.spend.ATTRIBUTION_RULE` —
    an entry's full cost lands on every id in its ``ids`` — and the report says so
    by name. An entry naming NO ids contributes to the totals and to the model and
    level cuts, but to no ticket row: there is no ticket to attribute it to, and
    inventing an "unattributed" ticket id would put a non-existent ticket in a list
    the frontend links to real ones.

    Response size is bounded by the number of distinct tickets, models and levels —
    not by the number of entries — because every entry folds into an existing
    bucket.
    """
    totals = _Bucket()
    by_ticket: dict[str, _Bucket] = {}
    by_model: dict[str, _Bucket] = {}
    by_level: dict[str, _Bucket] = {}

    for entry in entries:
        models = _models_of(entry)

        totals.costs.append(entry.cost_usd)
        totals.entries += 1
        totals.tokens.add(
            input=entry.tokens.input,
            output=entry.tokens.output,
            cache_read=entry.tokens.cache_read,
            cache_creation=entry.tokens.cache_creation,
            total=entry.tokens.total,
        )

        for ticket_id in dict.fromkeys(entry.ids):
            # ``dict.fromkeys`` de-duplicates while keeping order: an entry that
            # names the same ticket twice is one lane, and counting its cost twice
            # against that ticket would be double attribution to a single id —
            # which is not the rule, whose "each id" means each DISTINCT id.
            bucket = by_ticket.setdefault(ticket_id, _Bucket())
            bucket.costs.append(entry.cost_usd)
            bucket.entries += 1
            bucket.models.update(models)

        for model_id, cost, tokens in _model_contributions(entry):
            bucket = by_model.setdefault(model_id, _Bucket())
            bucket.costs.append(cost)
            bucket.entries += 1
            bucket.tokens.add(**tokens)

        level_bucket = by_level.setdefault(entry.level, _Bucket())
        level_bucket.costs.append(entry.cost_usd)
        level_bucket.entries += 1

    return SpendReport(
        totals=SpendTotals(
            costUsd=totals.cost(),
            entries=totals.entries,
            tokens=totals.tokens.frozen(),
        ),
        byTicket=[
            TicketSpend(
                ticketId=ticket_id,
                attributedCostUsd=cost,
                entries=bucket.entries,
                models=sorted(bucket.models),
            )
            for ticket_id, bucket, cost in _dearest_first(by_ticket)
        ],
        byModel=[
            ModelSpend(model=model_id, costUsd=cost, tokens=bucket.tokens.frozen())
            for model_id, bucket, cost in _dearest_first(by_model)
        ],
        byLevel=[
            LevelSpend(level=level, costUsd=cost, entries=bucket.entries)
            for level, bucket, cost in _dearest_first(by_level)
        ],
    )


def _models_of(entry: LedgerEntry) -> list[str]:
    """The model ids this entry names, verbatim, ``by_model`` first.

    Used for :attr:`~factory_console.domain.spend.TicketSpend.models` and, via
    :func:`_model_contributions`, for the by-model cut, so both cuts agree on which
    models an entry involved.
    """
    if entry.by_model:
        return list(entry.by_model)
    return [entry.model] if entry.model else []


def _model_contributions(
    entry: LedgerEntry,
) -> Iterable[tuple[str, float, dict[str, int]]]:
    """Yield ``(model_id, cost, token counts)`` for each model this entry spent on.

    Prefers the entry's ``by_model`` breakdown — the authoritative split, since one
    lane mixes several models — and falls back to the scalar ``model`` with the
    entry's own cost and tokens when there is no breakdown. An entry with neither
    yields nothing: its cost stays in the totals, where it is real, and is simply
    not claimed by any model, which is the honest reading of a record that names
    none.

    A ``by_model`` object carries no ``total``, so one is derived from its four
    counts; the fallback path uses the entry's own written total instead of
    re-deriving it, keeping the factory's measurement wherever there is one.
    """
    if entry.by_model:
        for model_id, spend in entry.by_model.items():
            yield (
                model_id,
                spend.cost_usd,
                {
                    "input": spend.input,
                    "output": spend.output,
                    "cache_read": spend.cache_read,
                    "cache_creation": spend.cache_creation,
                    "total": spend.input + spend.output + spend.cache_read + spend.cache_creation,
                },
            )
        return
    if entry.model:
        yield (
            entry.model,
            entry.cost_usd,
            {
                "input": entry.tokens.input,
                "output": entry.tokens.output,
                "cache_read": entry.tokens.cache_read,
                "cache_creation": entry.tokens.cache_creation,
                "total": entry.tokens.total,
            },
        )


def _dearest_first(buckets: dict[str, _Bucket]) -> list[tuple[str, _Bucket, float]]:
    """Order buckets by descending cost, then by key — the biggest bill first.

    Returns each bucket's summed cost alongside it so the figure is computed once
    and the row that is built carries exactly the number the ordering used.

    The key is the tie-break rather than insertion order so the response is a
    function of the entries alone: two ledgers holding the same records in a
    different order must serialise identically, or a client diffing them sees churn
    that is not spend.
    """
    priced = [(key, bucket, bucket.cost()) for key, bucket in buckets.items()]
    return sorted(priced, key=lambda item: (-item[2], item[0]))
