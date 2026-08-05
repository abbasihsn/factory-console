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
sub-microcent, and it is enough to carry the factory's own per-model figures
across unchanged: those are written to eight places or fewer. A lane's top-level
``cost_usd`` is NOT so bounded — it is itself a float sum of those per-model
figures, so the factory writes it with the trailing noise that produces (the
real line this repo's tests use as ground truth carries fifteen decimal places).
That noise is exactly what this rounding step is here to discard.

Like :mod:`factory_console.domain.graph`, these models are imported by full path
from their consumers and deliberately NOT re-exported from ``domain/__init__``, so
that aggregation file stays collision-free across the parallel v2.1 tickets — and
here that also keeps :class:`ModelSpend` from colliding, on import, with the
ledger's same-named raw ``by_model`` entry type.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from factory_console.domain.ledger import LedgerRead, SkipReason

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

Applied only where a response model is built. Eight places carries the factory's
per-model cost figures through unchanged — those are written to eight places or
fewer — while dropping the trailing binary noise of a float sum. A lane's own
``cost_usd`` routinely carries more places than that, being a float sum of the
per-model figures; the extra places are noise, not measurement. A per-entry
round, summed, would instead drift from the file by an amount that grows with
the ledger.
"""


class SpendTokens(BaseModel):
    """Summed token counts in the console's OWN camelCase wire vocabulary.

    A near-twin of :class:`~factory_console.domain.ledger.TokenCounts`, and
    deliberately NOT that class. The ledger's model is shaped by the FILE: it
    spells the factory's ``cache_read``/``cache_creation`` and it is
    ``extra="ignore"`` because another program owns that format. Both traits are
    wrong for a response — the REST v1 contract is camelCase, and a shape this repo
    builds AND serialises end to end takes the house ``extra="forbid"``.

    Reusing the ledger model here would publish snake_case keys in the one nested
    object of an otherwise camelCase body, and would leave the console's published
    contract to be rewritten by a future edit to a model that exists to track
    someone else's file. So the parse shape and the wire shape are separate types,
    converted in exactly one place —
    :meth:`~factory_console.domain.spend_calc._Tokens.frozen`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    input: int = 0
    output: int = 0
    cacheRead: int = 0
    cacheCreation: int = 0
    total: int = 0


class SpendTotals(BaseModel):
    """What the whole read cost: dollars, entry count, and summed token counts.

    ``entries`` counts the ledger entries that PARSED. It is deliberately reported
    beside the money so a caller can compare it against
    :attr:`SpendResponse.skipped` and see that a total was computed over 40 of 43
    lines — a partial bill that says so, rather than a confident wrong number.

    ``tokens`` sums the entries' own ``tokens`` objects field by field, including
    the factory's written ``total``; it is not recomputed from the parts, so it
    reports what the factory measured.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    costUsd: float = 0.0
    entries: int = 0
    tokens: SpendTokens = SpendTokens()


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

    ``tokens.total`` is the SUM of one total per contributing entry, and each of
    those is arrived at differently: an entry with a ``by_model`` breakdown carries
    no total there, so its contribution is derived (the four counts summed), while
    an entry with no breakdown contributes the entry's own written ``total``. A
    model fed by both kinds therefore reports a total that need NOT equal this
    row's other four fields summed — the factory's written total is its own
    measurement, not a restatement of the parts, and this row keeps it rather than
    quietly replacing it with a figure the ledger never recorded.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    costUsd: float = 0.0
    tokens: SpendTokens = SpendTokens()


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
    """Whether this project HAS a ledger, whether it could be READ, and where it is.

    THE field that keeps "no ledger" from being read as "$0.00". ``found: false``
    is the fresh-clone case and a client must be able to act on it directly, rather
    than inferring absence from a zero total — which an empty ledger also produces,
    and which for an unmeasured project would be a false statement about real money.

    ``read`` carries the SAME distinction one step further, because ``found`` alone
    does not survive a ledger that exists and could not be opened. A file over the
    reader's size cap, or one that cannot be stat'd or read at all, yields zero
    entries — so ``found: true`` over zeroed totals would state that a ledger WAS
    read and measured nothing, which for an 11 MiB file full of real lanes is the
    same false statement ``found`` exists to prevent, merely relocated. ``read:
    false`` says the bill is UNKNOWN rather than zero; the reason is in
    :attr:`SpendResponse.skipped`, at ``lineNo`` 0.

    So ``read`` means "the figures below came from actually reading a file", and it
    defaults to ``False`` — the honest value for the no-ledger case, where there was
    nothing to read. ``totals`` is a MEASURED zero only when ``read`` is true; on
    ``found: true, read: false`` it is a placeholder for a bill nobody could count.

    ``path`` is where the console LOOKED, not proof that anything was there — it
    is populated on all three outcomes, including ``found: false``. A view whose
    entire job in the no-ledger case is to explain the absence has to be able to
    name the place, and reading that off ``found`` instead would make the
    frontend restate the ledger's location in its own source. ``found`` and
    ``read``, not this field, are what say whether the file exists and was
    parsed. It is ``None`` only when the path could not be formed at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    found: bool
    read: bool = False
    path: str | None = None


# The skip reasons that belong to the WHOLE file rather than to any one line. T79
# records them at line 0 with zero entries, which is indistinguishable from an
# empty ledger by the totals alone — so they are named here and reported as
# ``SourceInfo.read: false``. See :func:`was_read`.
WHOLE_FILE_REASONS: frozenset[SkipReason] = frozenset({"file_too_large", "unreadable"})


def was_read(result: LedgerRead) -> bool:
    """Whether the ledger's CONTENT was actually examined.

    ``False`` for the two whole-file failures — over the size cap, or impossible to
    stat/read — where T79 returns zero entries because nothing was parsed, not
    because nothing was spent. Keyed on the reason rather than on ``line_no == 0``
    so a future per-line reason that happens to land at line 0 cannot silently turn
    a partial read into an unread one.

    Lives HERE, beside :class:`SourceInfo`, and not in the endpoint that calls it:
    it reads the domain's own :data:`~factory_console.domain.ledger.SkipReason`
    vocabulary to decide a domain-meaningful fact — "was this source measured" —
    which is the same rule ``spend_calc``'s module docstring states for attribution,
    "testable directly, at the level it is decided, instead of only through an HTTP
    round trip". As a private helper in ``api/v1/spend.py`` its only coverage was an
    HTTP round trip.
    """
    return not any(line.reason in WHOLE_FILE_REASONS for line in result.skipped)


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
    :attr:`SpendReport.totals` — **for per-line failures only**. A skip at
    ``lineNo`` 0 is not a line: it reports that the WHOLE file was not read
    (``unreadable``, or over the reader's size cap), so it stands for an unknown
    number of lines, not for one. That case is exactly ``source.read: false``, and a
    client counting unparsed lines must branch on it rather than render "1 line
    could not be read" over a ledger nothing ever opened.
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

        The iteration is filtered to :class:`SpendReport`'s OWN fields because this
        class extends it: a :class:`SpendResponse` satisfies the ``report``
        annotation, and splatting one wholesale would pass ``source`` (and every
        other field added here) twice — a ``TypeError`` at the one call site a type
        checker cannot flag, since the subclass is a legal argument. Filtering keeps
        re-wrapping an already-wrapped report a no-op on the read facts rather than
        a crash, and still costs this method nothing when a field is added to the
        base.
        """
        report_fields = {name: value for name, value in report if name in SpendReport.model_fields}
        return cls(
            source=source,
            skipped=list(skipped or []),
            skippedOmitted=skipped_omitted,
            **report_fields,
        )
