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

import logging
from collections.abc import Callable
from pathlib import Path

from factory_console.domain import Project, RunRecord
from factory_console.domain.runs import ArtifactRead
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.file_adapter.runs import (
    RECEIPTS_RELATIVE_DIR,
    RESULTS_RELATIVE_DIR,
    read_receipt,
    read_result,
)

_LOGGER = logging.getLogger(__name__)


class RunService:
    """Composes one :class:`RunRecord` per manifest ticket over a ``FileAdapter``.

    Constructed per request with the injected adapter; holds no state beyond it.
    """

    def __init__(self, adapter: FileAdapter) -> None:
        self._adapter = adapter

    @staticmethod
    def _safe_read(
        reader: Callable[[Path, str], ArtifactRead],
        project_root: Path,
        relative_dir: Path,
        ticket_id: str,
    ) -> ArtifactRead:
        """Read ONE artifact, degrading a path-unsafe manifest id to a named reason.

        ``read_result``/``read_receipt`` are total over filesystem and content
        failures but NOT over the id: they re-validate it through
        :func:`~factory_console.file_adapter.path_safety.validate_ticket_id_as_segment`,
        which raises
        :class:`~factory_console.file_adapter.path_safety.PathTraversal` on TWO
        independent rules — a :data:`~factory_console.domain.TICKET_ID_PATTERN`
        violation, and a bare ``.`` or ``..``. Only the first is impossible here.
        The pattern is ``^[A-Za-z0-9_.-]+$``, which admits ``.`` as an ordinary
        character, so a manifest entry whose id is ``.`` or ``..`` builds a
        perfectly valid :class:`~factory_console.domain.TicketSummary` and then
        raises on the read.

        Degraded rather than propagated, because this read happens once per
        MANIFEST ticket: letting it escape would fail the WHOLE listing with a 400
        naming an id the caller never supplied, deleting every healthy ticket's
        record to report one malformed neighbour. It is a ``@staticmethod`` for the
        same reason
        :meth:`~factory_console.file_adapter.real.RealFileAdapter._safe_run_state`
        is — the two are the same trade, made on the two paths that loop over the
        whole manifest, and they should read alike. The single-ticket reads keep
        the hard guard; only the per-ticket LOOP degrades.

        The reason is ``unreadable``, which is its established meaning: "the
        console refused to look at all", the branch
        :data:`~factory_console.domain.runs.ArtifactSkipReason` already names for a
        path that could not be proven safe. NOT ``absent`` — nothing here
        establishes that the factory wrote no artifact, and claiming it would be
        exactly the absent/unreadable collapse this milestone exists to remove.

        The refusal is LOGGED rather than degraded in silence: unlike ``absent`` on
        a fresh clone, an id the console will not touch is a malformed manifest,
        which is a real condition an operator should see. The ``run-record:``
        prefix names THIS layer — the ``runs:`` prefix belongs to
        :mod:`~factory_console.file_adapter.runs`, and reusing it here would
        attribute a composition-layer refusal to the reader that never ran.

        The reported path is the plain join, unresolved: the refusal happens before
        any filesystem access, so there is nothing to resolve and no resolution that
        could be trusted for an id already proven unsafe.
        """
        try:
            return reader(project_root, ticket_id)
        except PathTraversal:
            _LOGGER.warning(
                "run-record: %r is not a path-safe segment; its artifact is not read",
                ticket_id,
            )
            return ArtifactRead(
                path=project_root / relative_dir / f"{ticket_id}.json", reason="unreadable"
            )

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
        a failed request.

        Their one raising case, ``PathTraversal`` on a path-unsafe ticket id, IS
        reachable from here and is caught, per source, by :meth:`_safe_read`. The
        ids come off :class:`~factory_console.domain.TicketSummary`, whose ``id`` is
        :data:`~factory_console.domain.TICKET_ID_PATTERN`-constrained at the model
        boundary — but that constraint only rules out ONE of the two rules
        ``validate_ticket_id_as_segment`` enforces. It admits no path separator, and
        it equally admits a bare ``.`` or ``..``, which the reader rejects as a
        single-segment traversal. An earlier revision of this docstring called the
        branch "unreachable ... no input can reach and no test can honestly cover";
        both halves were wrong, and ``test_a_path_unsafe_manifest_id_...`` now covers
        it.

        Only the summaries' ids are used — a record is about the artifacts, and
        joining the ticket's own manifest fields onto it is the caller's business
        (the tickets endpoints already serve those).
        """
        return [
            RunRecord(
                ticketId=summary.id,
                result=self._safe_read(
                    read_result, project.rootPath, RESULTS_RELATIVE_DIR, summary.id
                ),
                receipt=self._safe_read(
                    read_receipt, project.rootPath, RECEIPTS_RELATIVE_DIR, summary.id
                ),
            )
            for summary in self._adapter.list_tickets(project)
        ]
