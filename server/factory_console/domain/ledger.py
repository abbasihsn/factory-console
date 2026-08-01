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

:class:`LedgerRead` and :class:`SkippedLine` are the console's OWN result types —
nothing on disk is parsed into them — so they keep the house ``extra="forbid"``.

:class:`LedgerRead` is also the absence-carrying half of the reader's contract.
"No ledger at all" is ``find_ledger_path`` returning ``None``; "an empty ledger"
is a :class:`LedgerRead` with zero ``entries``. They are different types from
different calls precisely so no caller can render a fresh clone (``.factory/`` is
gitignored, so it simply has no ledger) as "this project cost nothing".
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

SkipReason = Literal["not_json", "invalid_entry", "partial_line", "file_too_large"]
"""Why a line — or the whole file — was not turned into a :class:`LedgerEntry`.

- ``not_json`` — the line is not valid JSON.
- ``invalid_entry`` — valid JSON that does not validate as a :class:`LedgerEntry`.
- ``partial_line`` — the FINAL line, which had no terminating newline, failed to
  parse: the shape of a live append caught mid-write. It is named for that
  position only; a bad line anywhere else is ``not_json``/``invalid_entry``,
  because a reader cannot know from position alone what a line was meant to be.
- ``file_too_large`` — the file exceeded the reader's documented size cap and was
  not read at all. Recorded rather than silently short-read.
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
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    cost_usd: float = 0.0


class LedgerEntry(BaseModel):
    """One line of ``.factory/metrics/ledger.jsonl`` — one lane's spend record.

    ``ts``, ``agent``, ``level`` and ``cost_usd`` are required: a record naming no
    time, no producer, or no cost is not a spend record, and admitting it would
    put a zero-cost row in front of a human as though it were measured. Every
    other field is optional, because the factory does not write all of them for
    all lane kinds (a resumed lane has no ``review_tier``, a rolled-up level has
    no single ``model``).

    ``session_id`` is parsed so the record round-trips faithfully, but it is NOT
    surfaced to any API layer: it identifies a specific agent session and the
    console has no view that needs it.
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
    cost_usd: float
    cost_scope: str | None = None
    session_id: str | None = None
    review_tier: str | None = None
    by_model: dict[str, ModelSpend] = {}


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
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    entries: list[LedgerEntry] = []
    skipped: list[SkippedLine] = []
