"""The ``GET /api/v1/runs`` list + ``GET /api/v1/runs/{ticket_id}`` detail endpoints.

What the factory did, per ticket: run-state and PR url joined with the lane
result and the presence of a review receipt. Read-only — the console never writes
under ``.factory/``.

The list envelope leads with :class:`RunSources` rather than with the runs,
because absence is the hard part of this endpoint. ``.factory/`` is gitignored,
so a fresh clone has NONE of these artifacts and a bare list would be every
ticket with every field null — indistinguishable from "the factory ran and did
nothing". ``sources`` states, per artifact, whether it was found and where; and
each record's ``unavailable`` names the sources that did not answer for that
ticket. Together they make every null attributable.

Every path in a response is PROJECT-RELATIVE (:func:`_relative_to`): an absolute
path would disclose the server's filesystem layout, which this ticket's NFR
forbids, so a source that somehow resolves outside the project root reports
``found`` with a ``null`` path rather than leaking it.

The handlers do no error handling of their own. An invalid ``ticket_id`` is
rejected at the FastAPI ``Path`` boundary against ``TICKET_ID_PATTERN`` and
re-mapped to the ``invalid_ticket_id`` (400) envelope before the handler runs, so
no filesystem access happens for one; a
:class:`~factory_console.services.ticket_service.TicketNotFound` propagates to
the domain-error handler as the 404 envelope.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from factory_console.api.deps import TicketIdPath, get_file_adapter, get_run_artifact_reader
from factory_console.domain import Project
from factory_console.domain.run_record import (
    SOURCE_LAST_STOP,
    SOURCE_RECEIPTS,
    SOURCE_RESULTS,
    SOURCE_RUN_STATE,
    LastStop,
    RunRecord,
)
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.file_adapter.runs_protocol import RunArtifactReader
from factory_console.services.run_service import RunService

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the routes and their OpenAPI tag (mirrors ``api/v1/tickets.py``).
router = APIRouter(tags=["runs"])


class SourceInfo(BaseModel):
    """Whether one run artifact was found, and where — project-relative."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    found: bool
    path: str | None = None


class RunSources(BaseModel):
    """The four run artifacts' presence, reported per source.

    Named individually rather than as a map so the OpenAPI schema — and the
    frontend types generated from it — pin exactly which sources exist.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    runState: SourceInfo
    results: SourceInfo
    receipts: SourceInfo
    lastStop: SourceInfo


class RunListResponse(BaseModel):
    """Envelope for the runs list: the sources, one record per manifest ticket, last stop."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sources: RunSources
    runs: list[RunRecord]
    lastStop: LastStop | None = None


def _relative_to(path: Path | None, root: Path) -> SourceInfo:
    """Describe ``path`` relative to the project ``root``.

    ``None`` (the artifact is absent) is ``found=false`` with no path. A path that
    is not under ``root`` — which the probes cannot produce, but which a future
    caller could — is reported as found with a ``null`` path rather than as an
    absolute one: the NFR is that no out-of-root path appears in a response.
    """
    if path is None:
        return SourceInfo(found=False, path=None)
    try:
        return SourceInfo(found=True, path=path.relative_to(root).as_posix())
    except ValueError:
        return SourceInfo(found=True, path=None)


@router.get("/runs")
async def list_runs(
    request: Request,
    adapter: FileAdapter = Depends(get_file_adapter),
    runs: RunArtifactReader = Depends(get_run_artifact_reader),
) -> RunListResponse:
    """Return one run record per manifest ticket, with the sources they came from.

    The list is bounded by the manifest (one record per ticket, in manifest
    order); a run artifact naming an id the manifest does not carry contributes
    nothing. ``lastStop`` is ``null`` when the project has no ``last-stop.json``
    — ``sources.lastStop.found`` is the fact to read, since a present-but-opaque
    file yields an empty :class:`LastStop`, not ``null``.
    """
    root: Path = request.app.state.project_root
    project: Project = adapter.load_project(root)
    service = RunService(adapter, runs)
    sources = service.source_paths(project)
    return RunListResponse(
        sources=RunSources(
            runState=_relative_to(sources[SOURCE_RUN_STATE], project.rootPath),
            results=_relative_to(sources[SOURCE_RESULTS], project.rootPath),
            receipts=_relative_to(sources[SOURCE_RECEIPTS], project.rootPath),
            lastStop=_relative_to(sources[SOURCE_LAST_STOP], project.rootPath),
        ),
        runs=service.list_records(project),
        lastStop=service.read_last_stop(project),
    )


@router.get("/runs/{ticket_id}")
async def get_run(
    ticket_id: TicketIdPath,
    request: Request,
    adapter: FileAdapter = Depends(get_file_adapter),
    runs: RunArtifactReader = Depends(get_run_artifact_reader),
) -> RunRecord:
    """Return the :class:`RunRecord` for ``ticket_id``.

    ``ticket_id`` is validated at the ``Path`` boundary against the shared
    ``TICKET_ID_PATTERN``, so a ``../``-style id becomes the ``invalid_ticket_id``
    400 envelope without any filesystem access. A 404 means the MANIFEST has no
    such ticket; a ticket the manifest names but no run artifact mentions is a
    200 whose ``unavailable`` names each silent source.
    """
    root: Path = request.app.state.project_root
    project = adapter.load_project(root)
    return RunService(adapter, runs).get_record(project, ticket_id)
