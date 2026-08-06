"""The ``GET /api/v1/runs`` endpoint: the factory's per-ticket artifacts, listed.

The HTTP surface over T89's :class:`~factory_console.services.run_service.RunService`
and nothing else. The handler wires its two ports, loads the discovered project, and
returns what the service composed: one
:class:`~factory_console.domain.run_record.RunRecord` per MANIFEST ticket, in manifest
order. It adds no logic of its own, deliberately — every decision this listing makes
(the manifest is the list, a never-run ticket is still a record, an artifact-level
failure is a named reason rather than a failed request) belongs to the service and is
tested there, so a decision taken here would be a second copy of a rule with one
owner.

There is therefore no ARTIFACT-level error handling to do here. The two calls the
handler does make can still fail, and both are left to propagate exactly as on the
sibling endpoints: a :class:`~factory_console.file_adapter.discovery.ProjectNotFound`
from ``load_project``, or a
:class:`~factory_console.file_adapter.manifest.MalformedManifest` from the service's
``list_tickets``, reaches the domain-error handler ``create_app`` registers
(:func:`~factory_console.api.error_handlers.register_error_handlers`) and is rendered
at the status it declares. What cannot fail the listing is an ARTIFACT. Both of a
record's :class:`~factory_console.domain.runs.ArtifactRead` fields are TOTAL by the
:class:`~factory_console.file_adapter.run_artifacts.RunArtifactReader` port's
contract — a missing, unreadable, malformed, oversized or path-unsafe artifact
arrives as a named reason — so a project the factory has never run answers ``200``
with a full list of records whose sources all say ``absent``. It is NOT a 404 and NOT
an empty list: ``.factory/`` is gitignored, so having no artifacts is the normal state
of a fresh clone, and both of those answers would report the console's silence as the
manifest's.

The response is the ``{items, total}`` envelope, matching the two other v1 list
endpoints (``GET /api/v1/tickets`` and ``GET /api/v1/search``, both specified that way
in ``ARCHITECTURE.md``'s REST v1 section) rather than a bare JSON array. Consistency is
the whole argument — a client that already unwraps ``items`` for two lists should not
special-case a third — and the envelope is also the shape that can grow a sibling field
later without breaking every consumer, which a top-level array cannot.

**This module is the DISCLOSURE BOUNDARY for the two artifacts, and that is the one
piece of logic it does own.** The reading layer below is untyped on purpose — T88's
:class:`~factory_console.domain.runs.ArtifactRead` carries ``dict[str, Any]`` because
no captured real artifact exists to verify a schema against, and T89's
:class:`RunRecord` carries that verbatim — but "we do not know what is in this file"
is an argument for reading it loosely, never for PUBLISHING it whole. ``.factory``
artifacts are written by another process whose fields this repo cannot enumerate, so
serialising every key of one would disclose over HTTP whatever the factory happens to
put there (its own metrics carry session ids, model names and cost), for a view that
consumes two names. So the domain object stops here:
:meth:`ProjectedArtifactRead.from_artifact` rebuilds
it as a :class:`ProjectedArtifactRead` carrying only
:data:`DISCLOSED_ARTIFACT_FIELDS`, and it is that wire type — not
:class:`~factory_console.domain.run_record.RunRecord` — the envelope holds.

That is the same shape ``domain/spend.py`` already uses for the ledger: a house
``extra="forbid"`` response model DISTINCT from the raw read type, built by hand from
it, declining to project what no view asked for (see
:class:`~factory_console.domain.spend.SkippedLineInfo`, which refuses an 80-character
excerpt on exactly this ground). The rule both now obey is stated once in
``ARCHITECTURE.md``'s "Other factory artefacts (read-only)": a read-only endpoint MUST
NOT serialise an unmodelled, factory-written artefact verbatim — it may disclose only
the fields a real consumer needs, declared by name at the point of disclosure and
covered by a test. These wire models live HERE rather than in ``domain/`` precisely
because they exist only to narrow what leaves the process; putting them beside
:class:`ArtifactRead` would read as the domain having grown a schema, which is the
thing T88 declined to do.

Both of the handler's calls are BLOCKING filesystem work — ``load_project`` stats a
tree, and the service does two ``open``+read syscalls per manifest ticket — so they run
on a worker thread via ``anyio.to_thread.run_sync`` rather than inline on the event
loop. That is the house rule recorded under ``ARCHITECTURE.md``'s Cross-cutting
**Concurrency** bullet, and this endpoint is its first conversion because it does the
most per-request I/O of any route. The offload is at the HANDLER boundary only: the
:class:`FileAdapter` and :class:`RunArtifactReader` ports and
:class:`RunService` stay synchronous, which is what lets the rule be applied
per-endpoint without an async rewrite of the layer below.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import anyio.to_thread
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from factory_console.api.deps import get_file_adapter, get_run_artifact_reader
from factory_console.domain import RunRecord
from factory_console.domain.runs import ArtifactRead, ArtifactSkipReason
from factory_console.domain.ticket import TicketId
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.file_adapter.run_artifacts import RunArtifactReader
from factory_console.services.run_service import RunService

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag (mirrors ``api/v1/tickets.py``).
router = APIRouter(tags=["runs"])

DISCLOSED_ARTIFACT_FIELDS: tuple[str, ...] = ("pr_url", "status")
"""The ONLY keys of an artifact payload this endpoint will put on the wire.

The same two names ``PROJECTED_FIELDS`` declares in
``frontend/src/routes/runs/+page.svelte``, and deliberately readable as the same two:
the Runs view renders a PR column and an Outcome column, and those two columns are the
whole of what any consumer asks of a lane result. Both lists are UNVERIFIED guesses —
no captured artefact from a real factory run exists in this repo to check them against
(``tests/fixtures/runs/README.md``) — so neither is a schema, and neither claims to be.

They are two declarations rather than one because they answer different questions:
this one bounds what may LEAVE the process, the frontend's bounds what the view may
READ. Growing either is the same deliberate, reviewable one-place edit, and growing
the frontend's without growing this one now yields a field the view can name and the
server will not send — which is the intended failure mode, not an oversight: a key
reaches HTTP only by being written here, in a diff someone reviewed.
"""


class ProjectedArtifactRead(BaseModel):
    """One artifact read as it is DISCLOSED: the declared fields only, or the reason.

    The wire twin of :class:`~factory_console.domain.runs.ArtifactRead`, and
    deliberately not that class. The domain type carries ``dict[str, Any]`` because the
    reading layer models no field inside a factory-written file; this one carries
    ``dict[str, str]`` holding at most :data:`DISCLOSED_ARTIFACT_FIELDS`, because
    "unmodelled" is a reason to publish LESS, not everything. See the module docstring
    for why the narrowing lives at this boundary and not below it.

    ``path`` and ``reason`` cross unchanged: both are the console's own vocabulary — a
    path it computed and a reason it named — not content copied out of another
    program's file, so neither is a disclosure this rule is about, and both are what
    an operator acts on.

    :class:`ArtifactRead`'s invariant survives the projection: exactly one of ``data``
    and ``reason`` is set. A successful read whose payload declares none of the fields
    projects to ``data={}`` — an empty object, which is NOT ``None`` — so "read, and it
    named nothing this console recognises" stays distinct from "not read", exactly as
    it is one layer down. The invariant is not re-asserted with a validator here: this
    module is its only constructor and :meth:`from_artifact` is its only route, so a
    second copy of the rule would have one owner and two homes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    data: dict[str, str] | None = None
    reason: ArtifactSkipReason | None = None

    @classmethod
    def from_artifact(cls, artifact: ArtifactRead) -> ProjectedArtifactRead:
        """Narrow one domain :class:`ArtifactRead` to what may be disclosed.

        A declared key is disclosed only when it is present AND string-valued. A
        non-string under a declared name is OMITTED rather than coerced — the same
        "read a field or do not, never guess" rule the frontend's ``readString``
        applies — because ``str(value)`` on an object the console cannot model would
        publish a rendering of a payload it does not understand, which is the
        disclosure this projection exists to refuse. The frontend already reports a
        missing key as its own ignorance ("no status under any key this console
        recognises"), so an omission renders honestly.
        """
        if artifact.data is None:
            return cls(path=artifact.path, reason=artifact.reason)
        disclosed: dict[str, str] = {}
        for field in DISCLOSED_ARTIFACT_FIELDS:
            value = artifact.data.get(field)
            if isinstance(value, str):
                disclosed[field] = value
        return cls(path=artifact.path, data=disclosed)


class ProjectedRunRecord(BaseModel):
    """One manifest ticket's two artifacts, as disclosed — the wire twin of ``RunRecord``.

    Field for field :class:`~factory_console.domain.run_record.RunRecord`, with each
    :class:`ArtifactRead` replaced by its :class:`ProjectedArtifactRead`. The record's
    own rule is untouched and is the reason this type is a twin rather than a summary:
    both sources are still carried SEPARATELY, each with its own ``reason``, so no
    absence is flattened into a boolean or inferred from its neighbour.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticketId: TicketId
    result: ProjectedArtifactRead
    receipt: ProjectedArtifactRead

    @classmethod
    def from_record(cls, record: RunRecord) -> ProjectedRunRecord:
        """Project both of a composed record's sources through
        :meth:`ProjectedArtifactRead.from_artifact`.
        """
        return cls(
            ticketId=record.ticketId,
            result=ProjectedArtifactRead.from_artifact(record.result),
            receipt=ProjectedArtifactRead.from_artifact(record.receipt),
        )


class RunListResponse(BaseModel):
    """Envelope for the runs list: one record per manifest ticket, and their count.

    ``items`` are :class:`ProjectedRunRecord`, not the service's own
    :class:`RunRecord` — see the module docstring: the composed record is the domain's
    answer, and this envelope is what may be disclosed of it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[ProjectedRunRecord]
    total: int


@router.get("/runs")
async def list_runs(
    request: Request,
    adapter: FileAdapter = Depends(get_file_adapter),
    artifacts: RunArtifactReader = Depends(get_run_artifact_reader),
) -> RunListResponse:
    """Return one :class:`ProjectedRunRecord` per manifest ticket, in manifest order.

    Loads the discovered project from ``request.app.state.project_root`` and delegates
    the whole composition to :class:`RunService` over the injected
    :class:`FileAdapter` and :class:`RunArtifactReader`. Both calls are synchronous and
    hit the disk, so both are awaited through ``anyio.to_thread.run_sync`` — the
    coroutine yields for the duration, and every other route (the SSE stream above all)
    keeps being served while this one reads. ``functools.partial`` binds the arguments
    because ``run_sync`` passes positionals only and takes no keywords.

    ``total`` is the number of records, which is the manifest's ticket count and not a
    count of tickets that have artifacts — there is no filtering and NO PAGINATION, the
    same answer its sibling list endpoints give. That is a decision, not an omission:
    the list's length is the manifest's length, the manifest is a planning document an
    operator writes and reviews by hand (hundreds of entries at the outside, not
    millions), and it is already served whole by ``GET /api/v1/tickets``. Paging one of
    three list endpoints would split the envelope contract the SPA unwraps for all
    three, to bound a list nothing observed to be unbounded. What actually caps the
    cost is the offload above: the read is off the loop, so its size no longer stalls
    the rest of the app. Revisit if a real manifest ever makes the response slow — see
    ``ARCHITECTURE.md``'s Cross-cutting **Concurrency** bullet.

    The service's records are projected through :meth:`ProjectedRunRecord.from_record`
    before they are counted or returned. ``total`` is unaffected by the narrowing — it
    is a count of RECORDS, one per manifest ticket, and the projection changes what a
    record discloses, never how many there are.
    """
    root: Path = request.app.state.project_root
    project = await anyio.to_thread.run_sync(partial(adapter.load_project, root))
    records = await anyio.to_thread.run_sync(
        partial(RunService(adapter, artifacts).list_run_records, project)
    )
    items = [ProjectedRunRecord.from_record(record) for record in records]
    return RunListResponse(items=items, total=len(items))
