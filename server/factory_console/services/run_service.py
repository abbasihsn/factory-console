"""Runs list + detail application service.

:class:`RunService` composes the factory's ``.factory/`` artifacts into one
:class:`~factory_console.domain.run_record.RunRecord` per MANIFEST ticket, so the
HTTP handlers stay thin.

Two injected ports, no concrete implementations: the manifest and run-state come
through the read-only
:class:`~factory_console.file_adapter.protocol.FileAdapter`, and the run
artifacts through the sibling
:class:`~factory_console.file_adapter.runs_protocol.RunArtifactReader`.
``ARCHITECTURE.md`` fixes ``FileAdapter`` at eight methods that do not cover the
run artifacts, so widening it is not the move; the move is the narrow sibling
port :mod:`~factory_console.file_adapter.writer_protocol` already established for
the write path, which keeps this service on the same "domain models + Protocols
only" footing ``PROJECT_STRUCTURE.md`` requires of the backend track — and keeps
it unit-testable against fakes, like every sibling service.

The 404 for an id the manifest does not name is
:class:`~factory_console.services.ticket_service.TicketNotFound`, IMPORTED rather
than restated: it is the same fact ("this project has no such ticket") with the
same ``ticket_not_found`` code at the same status, and
:mod:`~factory_console.services.deps_service` and
:mod:`~factory_console.services.write_service` already reuse it the same way. A
second class for one fact could only drift from the first, and would slip past
any ``except TicketNotFound``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from factory_console.domain import Project, RunState
from factory_console.domain.run_record import (
    SOURCE_RECEIPTS,
    SOURCE_RESULTS,
    SOURCE_RUN_STATE,
    LastStop,
    PerTicketRunSourceName,
    RunRecord,
    RunResultSummary,
)
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.file_adapter.runs_protocol import RunArtifactReader
from factory_console.services.ticket_service import TicketNotFound


class RunService:
    """Composes run-state, lane results and receipts into per-ticket run records.

    Constructed per request with the injected ports; holds no state beyond them.
    """

    def __init__(self, adapter: FileAdapter, runs: RunArtifactReader) -> None:
        self._adapter = adapter
        self._runs = runs

    def source_paths(self, project: Project) -> Mapping[str, Path | None]:
        """Return each run artifact's absolute path, or ``None`` where it is absent.

        Keyed by :data:`~factory_console.domain.run_record.RUN_SOURCE_NAMES`. The
        caller renders these project-RELATIVE; the absolute paths never leave the
        server. "Found" means the artifact this console would actually READ — the
        reader reports a source it would refuse (one resolving outside the project
        root) as absent, so ``found`` cannot disagree with what was read.
        """
        return self._runs.source_paths(project)

    def read_last_stop(self, project: Project) -> LastStop | None:
        """Return the project's :class:`LastStop`, or ``None`` when the file is absent."""
        return self._runs.read_last_stop(project)

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
        ``list_tickets``, once in the run-artifact reader's ``read_pr_urls`` —
        because the states reach us through one port while the urls come through
        the other. Both go through the one parser, so they cannot disagree about
        the format.

        The artifact reads are BATCHED, not per-ticket: the reader is handed the
        whole id set once, so ``.factory/results`` and ``.factory/receipts`` are
        resolved once per request rather than once per ticket.
        """
        summaries = list(self._adapter.list_tickets(project))
        ticket_ids = [summary.id for summary in summaries]
        pr_urls = self._runs.read_pr_urls(project)
        results = self._runs.read_results(project, ticket_ids)
        receipts = self._runs.receipts_present(project, ticket_ids)
        return [
            self._compose(project, summary.id, summary.runState, pr_urls, results, receipts)
            for summary in summaries
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

        Parse count: for a JSON source this path reads ``run-state.json`` THREE
        times — once inside ``get_deps``' manifest projection, once in
        ``read_run_state``, once in the reader's ``read_pr_urls`` — one more than
        :meth:`list_records`, because the detail path resolves membership and
        state through two separate adapter calls.

        Raises:
            TicketNotFound: when the manifest does not name ``ticket_id``.
        """
        if self._adapter.get_deps(project, ticket_id) is None:
            raise TicketNotFound(ticket_id)
        ticket_ids = [ticket_id]
        return self._compose(
            project,
            ticket_id,
            self._adapter.read_run_state(project, ticket_id),
            self._runs.read_pr_urls(project),
            self._runs.read_results(project, ticket_ids),
            self._runs.receipts_present(project, ticket_ids),
        )

    def _compose(
        self,
        project: Project,
        ticket_id: str,
        run_state: RunState,
        pr_urls: Mapping[str, str],
        results: Mapping[str, RunResultSummary],
        receipts: frozenset[str],
    ) -> RunRecord:
        """Build one record, naming in ``unavailable`` every source that did not answer.

        A source is "unavailable" for this ticket whenever it produced nothing —
        the artifact is absent, holds no entry for this id, could not be read, or
        (for a path-unsafe manifest id) was refused before it was read. The cases
        are collapsed on purpose: the record's contract is that a null field is
        attributable to a NAMED source, not that the reason for each null is
        itemised. ``lastStop`` is never named here — it is a project-level fact,
        reported once by the list endpoint's ``sources``.

        ``runState`` is named for a SECOND reason besides an unknown state: only
        the JSON form of the run-state artifact carries PR urls, so on a project
        using the legacy marker-directory form ``prUrl`` is null for every ticket
        no matter what the factory did. Left unnamed, that null would read as "the
        factory opened no PR" — a fact — when it actually means "this run-state
        form cannot tell you". Naming the source keeps every null attributable,
        which is the whole contract of
        :class:`~factory_console.domain.run_record.RunRecord`.
        """
        result = results.get(ticket_id)
        has_receipt = ticket_id in receipts
        unavailable: list[PerTicketRunSourceName] = []
        if run_state is RunState.unknown or not self._carries_pr_urls(project):
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

    @staticmethod
    def _carries_pr_urls(project: Project) -> bool:
        """True if the project's run-state form can supply PR urls at all.

        Defers to :attr:`~factory_console.domain.run_state_source.RunStateSource.carriesPrUrls`
        rather than restating ``kind == "json"``, so this and
        :func:`~factory_console.file_adapter.runs.read_pr_urls` cannot drift into
        disagreeing about whether a null ``prUrl`` is attributable.

        A source that resolves OUTSIDE the project root is already ``None`` here:
        :func:`~factory_console.file_adapter.run_state.find_run_state_source`
        refuses it at resolution, so it supplies no states and no urls and this
        correctly names ``runState`` unavailable — rather than reporting a source
        the reader would refuse as one that answered.
        """
        source = project.runStateSource
        return source is not None and source.carriesPrUrls
