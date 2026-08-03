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

There is therefore no error handling to do and no error branch to take. Both of a
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
"""

from __future__ import annotations

from pathlib import Path

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
    :class:`FileAdapter` and :class:`RunArtifactReader`. ``total`` is the number of
    records, which is the manifest's ticket count and not a count of tickets that have
    artifacts — there is no filtering and no pagination.
    """
    root: Path = request.app.state.project_root
    project = adapter.load_project(root)
    items = RunService(adapter, artifacts).list_run_records(project)
    return RunListResponse(items=items, total=len(items))
