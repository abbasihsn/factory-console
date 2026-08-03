"""Run-record application service: compose the lane artifacts onto the manifest.

:class:`RunService` turns T88's two per-ticket readers into the console's
per-ticket view — one :class:`~factory_console.domain.run_record.RunRecord` for
every ticket the MANIFEST names. The manifest is the list; the artifacts under
``.factory/`` are evidence about it, so the output's length and order are the
manifest's, never the artifact directory's listing.

It depends on TWO read-only ports and on nothing concrete: the
:class:`~factory_console.file_adapter.protocol.FileAdapter` port for the manifest,
and the :class:`~factory_console.file_adapter.run_artifacts.RunArtifactReader` port
for the artifacts. Both are injected. This service opens no file and imports no
filesystem-touching module, per ``PROJECT_STRUCTURE.md``'s track ownership —
backend "depends only on domain models + FileAdapter Protocol", and the
file-adapter layer is "the ONLY layer that calls ``open()``".

That the artifact reads got their OWN small port, rather than two more methods on
``FileAdapter``, follows how this repo has twice already added a capability the
read port does not carry (``FileWriter``, ``FileWatcher``) — see
:mod:`~factory_console.file_adapter.run_artifacts`. Reading them through a port
rather than calling the module functions directly is what makes this service
substitutable: handed fakes, it touches no disk, so a caller can exercise a
POPULATED artifact without a real tree. Calling the readers directly would have
made every fake-backed test read the host filesystem at a ``rootPath`` that does
not exist and answer ``absent`` for every source while appearing to be under test.
"""

from __future__ import annotations

from factory_console.domain import Project, RunRecord
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.file_adapter.run_artifacts import RunArtifactReader


class RunService:
    """Composes one :class:`RunRecord` per manifest ticket over its two ports.

    Constructed per request with the injected adapter and artifact reader; holds no
    state beyond them.
    """

    def __init__(self, adapter: FileAdapter, artifacts: RunArtifactReader) -> None:
        self._adapter = adapter
        self._artifacts = artifacts

    def list_run_records(self, project: Project) -> list[RunRecord]:
        """Return one :class:`RunRecord` per manifest ticket, in manifest order.

        Every ticket ``adapter.list_tickets`` returns gets a record, including the
        ones the factory has never run: their sources come back ``absent``, which
        is an answer and is reported as one. Filtering to "tickets that have
        artifacts" would delete exactly the fact this milestone exists to show.

        NEVER raises for an artifact-level problem, and this method needs no
        try/except to promise that: TOTALITY IS THE
        :class:`~factory_console.file_adapter.run_artifacts.RunArtifactReader`
        PORT'S CONTRACT, so a missing, unreadable, malformed, oversized or
        path-unsafe source arrives as a named reason on the record rather than as a
        failed request. A path-unsafe manifest id — a bare ``.`` or ``..``, which
        :data:`~factory_console.domain.TICKET_ID_PATTERN` admits and
        ``validate_ticket_id_as_segment`` rejects — IS reachable here and is
        degraded by the port's implementation, one layer down where the other
        whole-manifest degrade
        (:meth:`~factory_console.file_adapter.real.RealFileAdapter._safe_run_state`)
        already lives. ``test_a_path_unsafe_manifest_id_...`` covers it end to end.

        Only the summaries' ids are used — a record is about the artifacts, and
        joining the ticket's own manifest fields onto it is the caller's business
        (the tickets endpoints already serve those).
        """
        return [
            RunRecord(
                ticketId=summary.id,
                result=self._artifacts.read_result(project, summary.id),
                receipt=self._artifacts.read_receipt(project, summary.id),
            )
            for summary in self._adapter.list_tickets(project)
        ]
