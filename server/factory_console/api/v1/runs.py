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
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.file_adapter.run_artifacts import RunArtifactReader
from factory_console.services.run_service import RunService

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag (mirrors ``api/v1/tickets.py``).
router = APIRouter(tags=["runs"])


class RunListResponse(BaseModel):
    """Envelope for the runs list: one record per manifest ticket, and their count."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[RunRecord]
    total: int


@router.get("/runs")
async def list_runs(
    request: Request,
    adapter: FileAdapter = Depends(get_file_adapter),
    artifacts: RunArtifactReader = Depends(get_run_artifact_reader),
) -> RunListResponse:
    """Return one :class:`RunRecord` per manifest ticket, in manifest order.

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
    """
    root: Path = request.app.state.project_root
    project = await anyio.to_thread.run_sync(partial(adapter.load_project, root))
    items = await anyio.to_thread.run_sync(
        partial(RunService(adapter, artifacts).list_run_records, project)
    )
    return RunListResponse(items=items, total=len(items))
