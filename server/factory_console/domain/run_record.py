"""What the factory did for one ticket, composed from its run artifacts.

A :class:`RunRecord` joins the four independently-optional artifacts the App
Factory leaves under ``.factory/`` — ``run-state.json`` (state + PR url),
``results/<id>.json`` (the lane result), ``receipts/<id>.json`` (presence only)
and ``last-stop.json`` — into ONE record per manifest ticket.

The point of this module is :attr:`RunRecord.unavailable`. In a fresh clone
``.factory/`` is gitignored and therefore absent, so every field of every record
would be ``null`` — which reads as "the factory ran and did nothing" rather than
"there is no run data here". ``unavailable`` NAMES each source that did not
answer for that ticket, so a null is always attributable: a field is null
*because* something named in ``unavailable`` was missing.

Schema-provenance note (read before adding a field)
---------------------------------------------------
The result and receipt schemas belong to the FACTORY, not to this console, so
this module models a small NAMED SUBSET and treats the rest as opaque
(``extra="ignore"``). Which fields are modelled, and why they are the ones
modelled, is documented on :class:`RunResultSummary` and :class:`LastStop`.

The short version, stated as the unverified claim it is: no real
``.factory/results`` / ``.factory/receipts`` / ``.factory/last-stop.json`` file
was reachable from the sandboxed build environment this was written in, and this
REPOSITORY documents no schema for any of them. :class:`RunResultSummary`'s field
list is taken from the App Factory's ``===LANE_RESULT===`` block — a contract
that lives in the FACTORY's own source, not here — so nothing in this repository
can confirm it. T81's Verification section asks for fields checked against a real
file; that check has not been performed, and until it is, a field here is
"believed to exist", not "shown to exist". Treat this module's provenance notes
as sourcing, not as verification, and re-derive the subset against a real lane
result when one becomes reachable.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from factory_console.domain.run_state import RunState

SOURCE_RUN_STATE = "runState"
SOURCE_RESULTS = "results"
SOURCE_RECEIPTS = "receipts"
SOURCE_LAST_STOP = "lastStop"

RUN_SOURCE_NAMES: tuple[str, ...] = (
    SOURCE_RUN_STATE,
    SOURCE_RESULTS,
    SOURCE_RECEIPTS,
    SOURCE_LAST_STOP,
)
"""The four run artifacts, named ONCE here.

These same strings are the keys of the list endpoint's ``sources`` object and the
values that appear in :attr:`RunRecord.unavailable`, so a caller can match "this
field is null" to "that source was not found" by string equality rather than by
convention.
"""


class RunResultSummary(BaseModel):
    """The named subset of a lane result (``.factory/results/<id>.json``).

    Provenance — WHY these fields and not others:

    - The factory owns this schema; the console reads a named subset and ignores
      the rest (``extra="ignore"``), so the factory can extend the file without
      breaking this parse. This mirrors T79's treatment of the metrics ledger.
    - NO real ``.factory/results/*.json`` file was reachable when this was
      written: ``.factory/`` is gitignored, so it exists only on the host that
      actually ran the factory and is not present in a build worktree.
      ``ARCHITECTURE.md`` documents no schema for it either. The fields below are
      therefore taken from the App Factory's ``===LANE_RESULT===`` block — the
      block the team-lead lane emits and persists to
      ``.factory/results/<ID>.json``, whose keys are ``id``, ``status``,
      ``pr_url``, ``route``, ``review_iterations``, ``verdict``, ``built``,
      ``review_summary``, ``unresolved``, ``handoff``, ``worktree``, ``spend``.
      Nothing outside that list is modelled and nothing in it is invented — but
      that list is NOT checkable from this repository (it lives in the factory's
      source, and the string appears nowhere here outside T81's own files), so it
      is an unverified source, not a verified one. If the real file disagrees,
      every modelled field degrades to ``None`` via the ``ValidationError`` branch
      in :func:`~factory_console.file_adapter.runs.read_result` and no test here
      would notice. Re-derive against a real lane result before relying on it.
    - Of those keys only the five below are SUMMARY material — the ones a runs
      list has to show. ``built`` / ``review_summary`` / ``unresolved`` /
      ``handoff`` / ``spend`` are lane-report prose and detail, and ``worktree``
      is an ABSOLUTE path on the machine that ran the factory, which this ticket
      forbids surfacing (no out-of-root paths in any response). They stay opaque.

    On-disk keys are snake_case and the REST contract is camelCase, so the
    snake_case names are read via ``validation_alias`` and serialized under the
    camelCase field names.
    """

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    status: str | None = None
    prUrl: str | None = Field(default=None, validation_alias="pr_url")
    route: str | None = None
    verdict: str | None = None
    reviewIterations: int | None = Field(default=None, validation_alias="review_iterations")


class LastStop(BaseModel):
    """Why the last factory run stopped (``.factory/last-stop.json``), minimally.

    Deliberately near-empty, for the same reason as :class:`RunResultSummary` but
    one step further: no real ``last-stop.json`` was reachable from the sandboxed
    build environment, AND — unlike the lane result — this console has no
    factory-side contract to ground a schema in. ``docs/planning/v2.1-PLAN.md``
    describes the file in exactly one line, "why the last run stopped", so
    ``reason`` is the ONE field modelled and every other key stays opaque
    (``extra="ignore"``). Guessing further would be inventing a schema and
    reporting the invention's misses as ``null`` — the failure this ticket's
    Context section exists to prevent.

    ``reason`` is ``None`` for a file that is present but does not carry a string
    ``reason``; presence is reported separately, by the endpoint's
    ``sources.lastStop``, so "the file is there but says nothing this console
    understands" stays distinguishable from "there is no file".
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    reason: str | None = None


class RunRecord(BaseModel):
    """What the factory did for one manifest ticket, across all its artifacts.

    ``hasReceipt`` is presence only: ``.factory/receipts/<id>.json`` either exists
    or does not. Receipt CONTENT is not modelled at all — this ticket asks for a
    boolean, and no real receipt file was reachable to model content from anyway
    (see :class:`RunResultSummary`).

    ``unavailable`` names, from :data:`RUN_SOURCE_NAMES`, every source that did
    not answer for THIS ticket — because the artifact is absent, because it holds
    no entry for this id, or because it could not be read. It is empty only when
    every per-ticket source answered.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticketId: str
    runState: RunState
    prUrl: str | None = None
    result: RunResultSummary | None = None
    hasReceipt: bool = False
    unavailable: list[str] = Field(default_factory=list)
