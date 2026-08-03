"""Run-record application service: compose the lane artifacts onto the manifest.

:class:`RunService` turns T88's two per-ticket readers into the console's
per-ticket view — one :class:`~factory_console.domain.run_record.RunRecord` for
every ticket the MANIFEST names. The manifest is the list; the artifacts under
``.factory/`` are evidence about it, so the output's length and order are the
manifest's, never the artifact directory's listing.

It depends on the read-only
:class:`~factory_console.file_adapter.protocol.FileAdapter` port for the manifest,
and calls :mod:`~factory_console.file_adapter.runs` directly for the artifacts.
That split is deliberate rather than an oversight: T88 shipped the readers as
plain module functions and did NOT add them to the port, and widening the port
here would force every implementer of it — including the in-memory fake — to
grow two methods for a read that is already total and already typed. When a
later ticket needs artifact reads to be fakeable independently of the filesystem,
that is the moment to put them on the port, not before.
"""

from __future__ import annotations

from factory_console.domain import Project, RunRecord
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.file_adapter.runs import read_receipt, read_result


class RunService:
    """Composes one :class:`RunRecord` per manifest ticket over a ``FileAdapter``.

    Constructed per request with the injected adapter; holds no state beyond it.
    """

    def __init__(self, adapter: FileAdapter) -> None:
        self._adapter = adapter

    def list_run_records(self, project: Project) -> list[RunRecord]:
        """Return one :class:`RunRecord` per manifest ticket, in manifest order.

        Every ticket ``adapter.list_tickets`` returns gets a record, including the
        ones the factory has never run: their sources come back ``absent``, which
        is an answer and is reported as one. Filtering to "tickets that have
        artifacts" would delete exactly the fact this milestone exists to show.

        NEVER raises for an artifact-level problem. ``read_result`` /
        ``read_receipt`` are total over filesystem and content failures — a
        missing, unreadable, malformed or oversized file becomes a named reason on
        the record — so a source-level problem is reported IN the response, not as
        a failed request. Their one raising case, ``PathTraversal`` on a malformed
        ticket id, is unreachable from here: these ids come off
        :class:`~factory_console.domain.TicketSummary`, whose ``id`` is already
        :data:`~factory_console.domain.TICKET_ID_PATTERN`-constrained at the model
        boundary, and that pattern admits no path separator. Catching it anyway
        would add a branch no input can reach and no test can honestly cover.

        Only the summaries' ids are used — a record is about the artifacts, and
        joining the ticket's own manifest fields onto it is the caller's business
        (the tickets endpoints already serve those).
        """
        return [
            RunRecord(
                ticketId=summary.id,
                result=read_result(project.rootPath, summary.id),
                receipt=read_receipt(project.rootPath, summary.id),
            )
            for summary in self._adapter.list_tickets(project)
        ]
