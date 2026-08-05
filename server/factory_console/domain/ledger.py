"""Typed spend records read from the factory's ``.factory/metrics/ledger.jsonl``.

The App Factory appends one JSON object per lane to that file; this module is the
console's typed view of it, plus the result type a read produces.

**These models are ``extra="ignore"``, the opposite of every other domain model
here (which is ``extra="forbid"``), and that is deliberate.** The ledger is
written by ANOTHER program on its own release cycle: the factory adds a field
whenever it starts measuring something new, and the console has no say in when.
``extra="forbid"`` would turn that additive, backward-compatible change into a
whole file of ``invalid_entry`` skips — today's console refusing tomorrow's
ledger over a field it does not even want to show. ``extra="forbid"`` remains
right for models the console itself owns end to end (an unknown key there IS a
bug); it is wrong for a file the console only observes.

That tolerance is about the SHAPE of a record, and it does not extend to the
VALUE of a field this console does arithmetic on. The two are separate axes and
only the first is forward compatibility: an unrecognised key is tomorrow's
factory measuring something new, whereas a non-finite ``cost_usd`` is not a
record from the future, it is a broken record now — and admitting it does not
cost that line, it costs every figure the line is summed into. So ``cost_usd``,
alone among these fields, is constrained (see :class:`LedgerEntry`). Strictness
here should stay that narrow: a value constraint earns its place only where a
bad value would spread beyond its own entry.

:class:`LedgerRead` and :class:`SkippedLine` are the console's OWN result types —
nothing on disk is parsed into them — so they keep the house ``extra="forbid"``.

:class:`LedgerRead` is also the absence-carrying half of the reader's contract.
"No ledger at all" is ``find_ledger_path`` returning ``None``; "an empty ledger"
is a :class:`LedgerRead` with zero ``entries``. They are different types from
different calls precisely so no caller can render a fresh clone (``.factory/`` is
gitignored, so it simply has no ledger) as "this project cost nothing".
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SkipReason = Literal["not_json", "invalid_entry", "partial_line", "file_too_large", "unreadable"]
"""Why a line — or the whole file — was not turned into a :class:`LedgerEntry`.

Per-line reasons:

- ``not_json`` — the line is not valid JSON.
- ``invalid_entry`` — valid JSON that does not validate as a :class:`LedgerEntry`.
- ``partial_line`` — the FINAL line, which had no terminating newline, failed to
  parse: the shape of a live append caught mid-write. It is named for that
  position only; a bad line anywhere else is ``not_json``/``invalid_entry``,
  because a reader cannot know from position alone what a line was meant to be.

Whole-file reasons, recorded at :attr:`SkippedLine.line_no` ``0`` because the
failure is the file's and not any one line's:

- ``file_too_large`` — the file exceeded the reader's documented size cap and was
  not read at all. Recorded rather than silently short-read.
- ``unreadable`` — the file could not be stat'd or read at all (permission
  denied, deleted between finding it and reading it, an I/O error). Distinct from
  ``not_json`` on purpose: no content was ever examined, so reporting a syntax
  reason would send a human looking for a malformed line in a file nothing could
  open, and the bill is unknown rather than corrupt.
"""


class TokenCounts(BaseModel):
    """The ``tokens`` object of one ledger entry: totals for a single lane.

    Every count defaults to ``0`` so an entry written before the factory measured
    a given bucket still parses, reporting an honest zero rather than failing.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    total: int = 0


class ModelSpend(BaseModel):
    """One model's share of a lane, as carried in the entry's ``by_model`` map.

    Keyed in ``by_model`` by the factory's full model id (e.g.
    ``claude-sonnet-5``), which the console treats as an opaque string — model
    ids are the factory's vocabulary, not a set this console may enumerate.

    ``cost_usd`` refuses a non-finite value for the reason given on
    :attr:`LedgerEntry.cost_usd`.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    cost_usd: float = Field(default=0.0, allow_inf_nan=False)


class LedgerEntry(BaseModel):
    """One line of ``.factory/metrics/ledger.jsonl`` — one lane's spend record.

    ``ts``, ``agent``, ``level`` and ``cost_usd`` are required: a record naming no
    time, no producer, or no cost is not a spend record, and admitting it would
    put a zero-cost row in front of a human as though it were measured. Every
    other field is optional, because the factory does not write all of them for
    all lane kinds (a resumed lane has no ``review_tier``, a rolled-up level has
    no single ``model``).

    ``session_id`` is parsed so the record round-trips faithfully in process, but
    it is NOT surfaced to any API layer: it identifies a specific agent session
    and the console has no view that needs it. That is enforced by
    ``exclude=True`` rather than by convention — this repo returns domain models
    straight out of its endpoints, so a comment asking the next lane not to
    serialise the field would be one ``-> LedgerEntry`` away from being ignored.
    The attribute still reads normally; it simply leaves no ``model_dump``.

    ``cost_usd`` additionally refuses a NON-FINITE value, and it is the only
    field here that does. ``json.loads`` accepts the bare literals ``NaN``,
    ``Infinity`` and ``-Infinity`` (Python's own ``json.dumps`` emits them for
    non-finite floats), and pydantic admits them into a ``float`` by default — so
    without this, one such line would validate, and ``math.fsum`` would carry the
    NaN into the project's total, every ticket row that entry touches, and its
    level row. That poisoned total arrives as a MEASURED figure with an empty
    ``skipped`` list, serialises as ``null`` through a schema that declares a
    number, and makes the by-cost ordering arbitrary (every comparison against
    NaN is false). Refusing it at parse time instead spends exactly the line it
    came from: the entry becomes an ``invalid_entry`` skip, which the reader
    already counts and the endpoint already publishes, so the total is visibly
    partial rather than quietly wrong. The narrowness is the point — it is
    ``extra="ignore"`` above that keeps tomorrow's ledger parsing, and this
    guards a corrupt value in a field this console does arithmetic on, not an
    unrecognised one. ``wall_min`` and the token counts are deliberately left
    alone: nothing sums them into a published figure, so a strange value there
    must not cost a real entry its place in the bill.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    ts: datetime
    agent: str
    level: str
    ids: list[str] = []
    model: str | None = None
    effort: str | None = None
    wall_min: float | None = None
    turns: int | None = None
    tokens: TokenCounts = TokenCounts()
    cost_usd: float = Field(allow_inf_nan=False)
    cost_scope: str | None = None
    session_id: str | None = Field(default=None, exclude=True)
    review_tier: str | None = None
    by_model: dict[str, ModelSpend] = {}

    @field_validator("ts")
    @classmethod
    def _require_an_instant(cls, value: datetime) -> datetime:
        """Read a naive ``ts`` as UTC so every entry is comparable.

        The factory writes ``...Z`` today, which parses tz-aware — but this is
        the console's ONLY datetime read from a file it does not own, and every
        datetime it builds itself is ``datetime.now(UTC)``. A naive ``ts``
        slipping in would not fail here; it would fail much later, the first time
        a consumer sorted or compared spend against a console-built instant, with
        a ``TypeError`` nowhere near the line that caused it. Assuming UTC is the
        honest reading: the factory's own format says UTC, so a value that omits
        the marker is missing a marker, not naming another zone.
        """
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class SkippedLine(BaseModel):
    """One line the reader could not turn into a :class:`LedgerEntry`, and why.

    A skipped line is never dropped silently — the whole point of the type is
    that a caller can say "12 entries, 1 unreadable line" instead of quietly
    under-reporting spend. ``excerpt`` is a TRUNCATED prefix of the offending
    line, for a human to recognise it by; it is truncated short and redacted so
    it can never carry a full ``session_id``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    line_no: int
    reason: SkipReason
    excerpt: str


class LedgerRead(BaseModel):
    """The result of reading ONE ledger file: what parsed, and what did not.

    ``entries`` may legitimately be empty — that is an EMPTY ledger, which is a
    different fact from a project having no ledger at all (``find_ledger_path``
    returning ``None``). No code path may collapse the two.

    ``skipped`` holds at most the reader's cap of DETAILED records. A file's byte
    size is capped, but its line COUNT is not implied by that cap — 10 MiB of
    newlines is ~10.5 million failing lines, and one model per line is gigabytes
    of memory for a read that is supposed to be bounded. So the detail list stops
    and ``skipped_omitted`` counts the rest. It is a count, not a silence: a
    caller reports ``len(skipped) + skipped_omitted`` unreadable lines and is
    never wrong about how much of the bill it could not see, only about which
    lines they were.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    entries: list[LedgerEntry] = []
    skipped: list[SkippedLine] = []
    skipped_omitted: int = 0
