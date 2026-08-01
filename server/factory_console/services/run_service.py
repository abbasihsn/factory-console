"""Runs list + detail application service.

:class:`RunService` composes the factory's four ``.factory/`` artifacts into one
:class:`~factory_console.domain.run_record.RunRecord` per MANIFEST ticket, so the
HTTP handlers stay thin. It owns :class:`RunTicketNotFound` — the 404 for an id
the manifest does not name — co-located here per the ``errors.py`` convention
that a :class:`~factory_console.errors.FactoryConsoleError` subclass lives where
it is raised.

Two sources, deliberately: the manifest and run-state come through the read-only
:class:`~factory_console.file_adapter.protocol.FileAdapter`, while the run
artifacts come from the file-adapter's :mod:`~factory_console.file_adapter.runs`
module directly — the ``FileAdapter`` protocol is a fixed eight-method contract
in ``ARCHITECTURE.md`` that does not cover them, and widening a shared port (plus
both implementations) for one read-only v2.1 surface would be a bigger change
than the seam is worth.

Be clear about what that costs, because it is NOT the pattern used elsewhere:
:mod:`~factory_console.services.events_service` and
:mod:`~factory_console.services.write_service` each depend on an injected
abstraction (``FileWatcher`` via ``Depends(get_file_watcher)``, ``FileWriter``
via ``Depends(get_file_writer)``) and import the file-adapter only for its TYPE.
This module is the first service to import a file-adapter IMPLEMENTATION module
and call its functions, with no DI seam — so the run artifacts always come from
the real filesystem regardless of which adapter is injected, and they cannot be
faked. The established alternative, if this surface grows, is a sibling port
(:mod:`~factory_console.file_adapter.writer_protocol` is the worked example) with
its own fake and ``deps`` provider, not a wider ``FileAdapter``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from factory_console.domain import Project, RunState
from factory_console.domain.run_record import (
    SOURCE_LAST_STOP,
    SOURCE_RECEIPTS,
    SOURCE_RESULTS,
    SOURCE_RUN_STATE,
    LastStop,
    RunRecord,
)
from factory_console.errors import FactoryConsoleError
from factory_console.file_adapter import runs as runs_adapter
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.protocol import FileAdapter


class RunTicketNotFound(FactoryConsoleError):
    """Raised when a run record is asked for an id absent from the manifest.

    Carries the ``ticket_not_found`` code at HTTP 404 — the SAME code
    :class:`~factory_console.services.ticket_service.TicketNotFound` uses, because
    it is the same fact ("this project has no such ticket") and a client should not
    have to special-case the endpoint it asked. Distinct from a ticket the manifest
    DOES name but the run-state does not: that is a 200 with a record whose
    ``unavailable`` names ``runState``.
    """

    def __init__(self, ticket_id: str) -> None:
        super().__init__(
            code="ticket_not_found",
            message=f"Ticket {ticket_id!r} not found",
            status=404,
            details=None,
        )


class RunService:
    """Composes run-state, lane results and receipts into per-ticket run records.

    Constructed per request with the injected adapter; holds no state beyond it.
    """

    def __init__(self, adapter: FileAdapter) -> None:
        self._adapter = adapter

    def source_paths(self, project: Project) -> Mapping[str, Path | None]:
        """Return each run artifact's absolute path, or ``None`` where it is absent.

        Keyed by :data:`~factory_console.domain.run_record.RUN_SOURCE_NAMES`. The
        caller renders these project-RELATIVE; the absolute paths never leave the
        server. ``runState`` reports the source the project resolved at discovery
        (the factory's JSON or a legacy marker directory), so "found" here means
        the artifact this console would actually read.
        """
        root = project.rootPath
        source = project.runStateSource
        return {
            SOURCE_RUN_STATE: source.path if source is not None else None,
            SOURCE_RESULTS: runs_adapter.find_results_dir(root),
            SOURCE_RECEIPTS: runs_adapter.find_receipts_dir(root),
            SOURCE_LAST_STOP: runs_adapter.find_last_stop_file(root),
        }

    def read_last_stop(self, project: Project) -> LastStop | None:
        """Return the project's :class:`LastStop`, or ``None`` when the file is absent."""
        return runs_adapter.read_last_stop(project.rootPath)

    def list_records(self, project: Project) -> list[RunRecord]:
        """Return one :class:`RunRecord` per manifest ticket, in manifest order.

        Bounded by the manifest's ticket count, per the ticket's NFR — a run
        artifact for an id the manifest does not name contributes no record.

        Run-state comes from the summary the ADAPTER already resolved, never from
        a resolver built here. Two reasons, both load-bearing: it keeps this
        endpoint on the same authority as :meth:`get_record` and as
        ``GET /api/v1/tickets`` (a resolver built here would answer from the real
        filesystem even when a non-filesystem adapter is injected, so list and
        detail would disagree about the same ticket); and the adapter's
        projection already degrades a path-unsafe manifest id to
        :attr:`RunState.unknown` instead of failing the whole listing with a 400
        that names no bad input.

        Parse count, stated plainly rather than claimed away: for a JSON source
        this reads ``run-state.json`` TWICE per request — once inside
        ``list_tickets``, once in :func:`~factory_console.file_adapter.runs.read_pr_urls`
        — because the states reach us through the port while the urls do not.
        Both go through the one parser, so they cannot disagree about the format.
        """
        pr_urls = runs_adapter.read_pr_urls(project.runStateSource)
        return [
            self._compose(project, summary.id, summary.runState, pr_urls)
            for summary in self._adapter.list_tickets(project)
        ]

    def get_record(self, project: Project, ticket_id: str) -> RunRecord:
        """Return the :class:`RunRecord` for ``ticket_id``.

        Membership is decided by the MANIFEST — ``adapter.get_deps`` returning
        ``None`` is the 404 — never by whether the run artifacts mention the id: a
        ticket the factory has not touched yet is a real ticket with no run data,
        which is exactly what ``unavailable`` is for.

        ``get_deps`` and not ``get_ticket``, though only the ``is None`` is used:
        ``get_ticket`` answers membership by READING AND RENDERING the ticket's
        ``.md`` body, so a manifest ticket whose body is missing raises
        ``TicketFileMissing`` (404 ``ticket_file_missing``) and an unreadable one
        raises ``TicketFileUnreadable`` (500) — turning "the factory has no run
        data for this ticket", the 200 this endpoint exists to serve, into an
        error about a markdown file it never needed. ``get_deps`` stays inside the
        manifest projection and touches no ``.md`` at all.

        Raises:
            RunTicketNotFound: when the manifest does not name ``ticket_id``.
        """
        if self._adapter.get_deps(project, ticket_id) is None:
            raise RunTicketNotFound(ticket_id)
        return self._compose(
            project,
            ticket_id,
            self._adapter.read_run_state(project, ticket_id),
            runs_adapter.read_pr_urls(project.runStateSource),
        )

    def _compose(
        self,
        project: Project,
        ticket_id: str,
        run_state: RunState,
        pr_urls: Mapping[str, str],
    ) -> RunRecord:
        """Build one record, naming in ``unavailable`` every source that did not answer.

        A source is "unavailable" for this ticket whenever it produced nothing —
        the artifact is absent, holds no entry for this id, or could not be read.
        The three cases are collapsed on purpose: the record's contract is that a
        null field is attributable to a NAMED source, not that the reason for each
        null is itemised. ``lastStop`` is never named here — it is a
        project-level fact, reported once by the list endpoint's ``sources``.

        A path-unsafe manifest id is a FOURTH way to produce nothing, and it is
        caught rather than raised for the same reason
        :meth:`~factory_console.file_adapter.real.RealFileAdapter._safe_run_state`
        catches it: ``TICKET_ID_PATTERN`` admits a bare ``.``/``..``, so one such
        id in the manifest would otherwise fail the WHOLE list with a 400 naming
        no bad input, when the honest answer is a record for that ticket with
        every per-ticket source unavailable. The single-ticket path stays strict —
        ``get_record``'s id comes from the URL and is rejected at the boundary.
        """
        root = project.rootPath
        try:
            result = runs_adapter.read_result(root, ticket_id)
            has_receipt = runs_adapter.has_receipt(root, ticket_id)
        except PathTraversal:
            result, has_receipt = None, False
        unavailable: list[str] = []
        if run_state is RunState.unknown:
            unavailable.append(SOURCE_RUN_STATE)
        if result is None:
            unavailable.append(SOURCE_RESULTS)
        if not has_receipt:
            unavailable.append(SOURCE_RECEIPTS)
        return RunRecord(
            ticketId=ticket_id,
            runState=run_state,
            prUrl=pr_urls.get(ticket_id),
            result=result,
            hasReceipt=has_receipt,
            unavailable=unavailable,
        )
