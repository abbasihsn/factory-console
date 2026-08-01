# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.
"""Filesystem-backed :class:`RunArtifactReader` — the real run-artifact port.

The thin object wrapper over :mod:`~factory_console.file_adapter.runs`' free
functions, standing to :class:`~factory_console.file_adapter.runs_protocol.RunArtifactReader`
exactly as :class:`~factory_console.file_adapter.real_writer.RealFileWriter` stands
to ``FileWriter``. All the reading rules — absence degrades instead of raising,
containment is checked on the RESOLVED path, an unsafe id is refused before any
join — stay in ``runs.py``; this module only adds the port's shape: the resolved
:class:`~factory_console.domain.project.Project` in, one resolution of each
artifact directory per request, and ids that could not be read left OUT of the
answer rather than represented in it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

from factory_console.domain import Project
from factory_console.domain.run_record import (
    SOURCE_LAST_STOP,
    SOURCE_RECEIPTS,
    SOURCE_RESULTS,
    SOURCE_RUN_STATE,
    LastStop,
    RunResultSummary,
)
from factory_console.file_adapter import runs
from factory_console.file_adapter.path_safety import PathTraversal

_LOGGER = logging.getLogger(__name__)


class RealRunArtifactReader:
    """Reads the factory's run artifacts from the real filesystem.

    Stateless and cheap to construct; ``create_app`` binds one for the process.
    """

    def source_paths(self, project: Project) -> Mapping[str, Path | None]:
        """Return each run artifact's absolute path, or ``None`` where it is absent.

        ``runState`` goes through the same containment probe as the other three
        (:func:`~factory_console.file_adapter.runs.find_run_state_path`) rather
        than being reported straight off
        :attr:`~factory_console.domain.project.Project.runStateSource`, so a
        source that resolves outside the project root reports absent here instead
        of reporting a lexical, still-in-root-looking path for a file the reader
        would refuse to read.
        """
        root = project.rootPath
        return {
            SOURCE_RUN_STATE: runs.find_run_state_path(project.runStateSource, root),
            SOURCE_RESULTS: runs.find_results_dir(root),
            SOURCE_RECEIPTS: runs.find_receipts_dir(root),
            SOURCE_LAST_STOP: runs.find_last_stop_file(root),
        }

    def read_last_stop(self, project: Project) -> LastStop | None:
        """Return the project's :class:`LastStop`, or ``None`` when the file is absent."""
        return runs.read_last_stop(project.rootPath)

    def read_pr_urls(self, project: Project) -> Mapping[str, str]:
        """Return ``{ticket_id: pr_url}`` from the project's run-state source."""
        return runs.read_pr_urls(project.runStateSource, project.rootPath)

    def read_results(
        self, project: Project, ticket_ids: Sequence[str]
    ) -> Mapping[str, RunResultSummary]:
        """Return the lane result for each id that has a readable one.

        ``.factory/results`` is resolved ONCE here and reused across every id —
        the whole reason the port is batched. The project root is resolved once
        here for the same reason: every id's containment check compares against
        it, and re-deriving an invariant root per ticket walks and stats its whole
        component chain N times for one answer.
        """
        root = project.rootPath
        resolved_root = self._resolve_root(root)
        results_dir = runs.find_results_dir(root)
        found: dict[str, RunResultSummary] = {}
        for ticket_id in ticket_ids:
            try:
                summary = runs.read_result_in(
                    results_dir, root, ticket_id, resolved_root=resolved_root
                )
            except PathTraversal:
                self._log_unsafe_id(ticket_id, "lane result")
                continue
            if summary is not None:
                found[ticket_id] = summary
        return found

    def receipts_present(self, project: Project, ticket_ids: Sequence[str]) -> frozenset[str]:
        """Return the subset of ``ticket_ids`` that have a review receipt.

        Resolves ``.factory/receipts`` and the project root once, for the same
        reason as :meth:`read_results`.
        """
        root = project.rootPath
        resolved_root = self._resolve_root(root)
        receipts_dir = runs.find_receipts_dir(root)
        present: set[str] = set()
        for ticket_id in ticket_ids:
            try:
                if runs.has_receipt_in(receipts_dir, root, ticket_id, resolved_root=resolved_root):
                    present.add(ticket_id)
            except PathTraversal:
                self._log_unsafe_id(ticket_id, "receipt")
        return frozenset(present)

    @staticmethod
    def _resolve_root(root: Path) -> Path | None:
        """Pre-resolve the project root for the batched containment checks.

        ``None`` on failure, NOT a fallback root: ``resolve()`` raises ``OSError``
        (an unreadable component) or ``RuntimeError`` (a symlink loop) exactly
        where containment cannot be proven, and
        :func:`~factory_console.file_adapter.path_safety.is_contained` already
        turns both into "not contained". Passing ``None`` makes it re-resolve and
        reach that same refusal per ticket, so the optimisation degrades to the
        unoptimised path rather than substituting a root that could widen it.
        """
        try:
            return root.resolve()
        except (OSError, RuntimeError):
            return None

    @staticmethod
    def _log_unsafe_id(ticket_id: str, artifact: str) -> None:
        """Record that a path-unsafe id was refused, then leave it out of the answer.

        The ids here come from the MANIFEST, not from a URL, and
        ``TICKET_ID_PATTERN`` admits a bare ``.``/``..`` — so one such id must not
        fail the whole listing with a 400 that names no bad input. Dropping it is
        the right answer, but dropping it SILENTLY would make a security control
        firing indistinguishable from an ordinary "no artifacts here", leaving an
        operator no trace to investigate. ``%r`` because the id is arbitrary
        file-sourced text and an unescaped newline in it would forge log records.
        """
        _LOGGER.warning(
            "%s: manifest id %r is not a safe path segment; reporting it unavailable",
            artifact,
            ticket_id,
        )
