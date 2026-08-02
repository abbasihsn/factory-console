"""The console's own spend projection: the ledger aggregated into three cuts.

These are the shapes ``GET /api/v1/spend`` publishes, built from T79's
:class:`~factory_console.domain.ledger.LedgerEntry` records. Unlike the ledger
models — which are ``extra="ignore"`` because another program writes that file —
every model here is owned by the console end to end, so they keep the house
``extra="forbid"``: an unknown key in a shape this repo both builds and serialises
IS a bug.

Three things this module fixes rather than leaves to its callers.

**The attribution rule is a value, not a convention.** A ledger entry's ``ids`` is
a LIST — one lane may cover several tickets — so per-ticket cost requires a choice.
This program attributes an entry's FULL cost to EVERY id it names
(:data:`ATTRIBUTION_RULE`), which means the attributed costs may sum to more than
the true total; the alternative, splitting evenly, invents a precision the ledger
does not have. Because a client cannot tell the two apart from the numbers, the
rule travels WITH the numbers in :attr:`SpendReport.attribution` rather than only
in this docstring.

**Absence is not zero.** :class:`SourceInfo` carries ``found`` separately from any
total, so a fresh clone (``.factory/`` is gitignored, so it simply has no ledger)
is never rendered as "this project cost nothing". A zero total and an unread
ledger are different responses; ``found`` is the field that says which.

**Money rounds once, at the boundary.** :data:`COST_DECIMAL_PLACES` is applied
here, where the response is built, and never per entry — rounding each entry and
then summing is how a total drifts from the file it came from. Eight places is
sub-microcent and preserves the factory's own figures verbatim (it writes at most
eight), while still discarding the binary noise a long ``fsum`` leaves behind.

Like :mod:`factory_console.domain.graph`, these models are imported by full path
from their consumers and deliberately NOT re-exported from ``domain/__init__``, so
that aggregation file stays collision-free across the parallel v2.1 tickets — and
here that also keeps :class:`ModelSpend` from colliding, on import, with the
ledger's same-named raw ``by_model`` entry type.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from factory_console.domain.ledger import SkipReason, TokenCounts

AttributionRule = Literal["full-to-each-id"]
"""How an entry's cost is spread over the several ticket ids it may name."""

ATTRIBUTION_RULE: AttributionRule = "full-to-each-id"
"""The one rule this program implements, reported by name in every response.

``full-to-each-id``: an entry naming ``["T1", "T2"]`` contributes its WHOLE cost to
``T1`` and its WHOLE cost to ``T2``. Per-ticket figures are therefore *attributed*
cost, and their sum may exceed :attr:`SpendTotals.costUsd` — which is honest, where
an even split would report two figures the ledger never measured.
"""

COST_DECIMAL_PLACES = 8
"""Decimal places every dollar figure in this module is rounded to, ONCE.

Applied only where a response model is built. Eight places keeps the factory's own
cost figures bit-identical (it writes no more than eight) while dropping the
trailing binary noise of a float sum — a per-entry round, summed, would instead
drift from the file by an amount that grows with the ledger.
"""


class SpendTotals(BaseModel):
    """What the whole read cost: dollars, entry count, and summed token counts.

    ``entries`` counts the ledger entries that PARSED. It is deliberately reported
    beside the money so a caller can compare it against
    :attr:`SpendReport.skipped` and see that a total was computed over 40 of 43
    lines — a partial bill that says so, rather than a confident wrong number.

    ``tokens`` sums the entries' own ``tokens`` objects field by field, including
    the factory's written ``total``; it is not recomputed from the parts, so it
    reports what the factory measured.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    costUsd: float = 0.0
    entries: int = 0
    tokens: TokenCounts = TokenCounts()


class TicketSpend(BaseModel):
    """One ticket id's attributed spend, under :data:`ATTRIBUTION_RULE`.

    ``attributedCostUsd`` is named for the rule: it is the sum of the FULL cost of
    every entry naming this ticket, so summing this field across tickets does not
    give :attr:`SpendTotals.costUsd` and is not meant to.

    ``models`` lists the model ids seen on this ticket's entries, sorted, as the
    factory wrote them — a set for the view to render, not a per-model breakdown
    (that is :class:`ModelSpend`, which is project-wide).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticketId: str
    attributedCostUsd: float = 0.0
    entries: int = 0
    models: list[str] = Field(default_factory=list)


class ModelSpend(BaseModel):
    """One model's project-wide share of the bill.

    ``model`` is the factory's model id VERBATIM (e.g. ``claude-opus-4-8[1m]``).
    Nothing here maps it to a display name: mapping is the view's job, and a
    mapping applied at this layer would silently hide a model the console has not
    heard of behind whatever its fallback happened to be.

    Distinct from the ledger's same-named
    :class:`~factory_console.domain.ledger.ModelSpend`, which is the RAW shape of
    one ``by_model`` value on one entry. This is the aggregate across every entry.

    ``tokens.total`` is derived (the four counts summed) because a ``by_model``
    object carries no total of its own — except where a bucket was filled from an
    entry that had no ``by_model`` breakdown at all, in which case the entry's own
    written ``total`` is used.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    costUsd: float = 0.0
    tokens: TokenCounts = TokenCounts()


class LevelSpend(BaseModel):
    """One agent level's share of the bill — how much of it was review, not build.

    ``level`` is the ledger's ``level`` field as written (``ticket``, and whatever
    else the factory names): the vocabulary belongs to the factory, so no closed
    set is enumerated here and an unrecognised level appears rather than vanishing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: str
    costUsd: float = 0.0
    entries: int = 0


class SkippedLineInfo(BaseModel):
    """A ledger line that did not parse, over HTTP: which line, and why.

    T79's :class:`~factory_console.domain.ledger.SkippedLine` also carries an
    ``excerpt`` of the offending line; it is NOT projected here. The excerpt exists
    for a human reading the file, and the ledger is a file the console only
    observes — putting a slice of its raw bytes on the wire would widen what this
    read-only endpoint can disclose for no view that needs it.

    ``lineNo`` is ``0`` when the failure belongs to the whole file rather than to
    any one line (unreadable, or over the reader's size cap).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lineNo: int
    reason: SkipReason


class SourceInfo(BaseModel):
    """Whether this project HAS a ledger, and where it is.

    THE field that keeps "no ledger" from being read as "$0.00". ``found: false``
    is the fresh-clone case and a client must be able to act on it directly, rather
    than inferring absence from a zero total — which an empty ledger also produces,
    and which for an unmeasured project would be a false statement about real money.

    ``path`` is the resolved ledger path when ``found``, and ``None`` when not.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    found: bool
    path: str | None = None


class SpendReport(BaseModel):
    """The pure aggregate of a list of ledger entries: totals and the three cuts.

    What :func:`~factory_console.domain.spend_calc.aggregate` returns, and nothing
    more. It carries no ``source`` and no ``skipped`` because the aggregator is
    given entries and cannot know whether an empty list came from an empty ledger
    or from no ledger at all — the caller knows that, and adds it in
    :class:`SpendResponse`. Keeping the two apart is what stops the pure function
    from inventing an answer to a question it was never asked.

    Every list is sorted by descending cost, then by name, so equal-cost rows have
    a stable order and a client can render the top spenders without re-sorting.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    attribution: AttributionRule = ATTRIBUTION_RULE
    totals: SpendTotals = SpendTotals()
    byTicket: list[TicketSpend] = Field(default_factory=list)
    byModel: list[ModelSpend] = Field(default_factory=list)
    byLevel: list[LevelSpend] = Field(default_factory=list)


class SpendResponse(SpendReport):
    """The ``GET /api/v1/spend`` body: a :class:`SpendReport` plus how it was read.

    Extends the pure report with the two facts only the caller holds — whether the
    ledger existed at all (:class:`SourceInfo`) and which of its lines could not be
    read — by SUBCLASSING it, so the wire shape cannot drift from the aggregate as
    fields are added to one and not the other.

    ``skippedOmitted`` counts lines that failed beyond the reader's detail cap. It
    is not decoration: T79 caps how many skipped lines it materialises but keeps
    counting them, and dropping that count here would make a catastrophically
    corrupt ledger report exactly as partial as a mildly corrupt one.
    ``len(skipped) + skippedOmitted`` is the exact number of lines missing from
    :attr:`SpendReport.totals`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceInfo
    skipped: list[SkippedLineInfo] = Field(default_factory=list)
    skippedOmitted: int = 0

    @classmethod
    def from_report(
        cls,
        report: SpendReport,
        *,
        source: SourceInfo,
        skipped: list[SkippedLineInfo] | None = None,
        skipped_omitted: int = 0,
    ) -> SpendResponse:
        """Wrap a pure ``report`` with the read facts the aggregator could not know.

        Copies the report's fields across by iteration rather than by name so a
        field added to :class:`SpendReport` reaches the wire without this method
        needing to hear about it.
        """
        return cls(
            source=source,
            skipped=list(skipped or []),
            skippedOmitted=skipped_omitted,
            **dict(report),
        )
