"""In-memory :class:`RunArtifactReader` — the deterministic test double.

The read-path twin of :class:`~factory_console.file_adapter.fake_writer.FakeFileWriter`:
it satisfies :class:`~factory_console.file_adapter.runs_protocol.RunArtifactReader`
structurally while touching no filesystem, so
:class:`~factory_console.services.run_service.RunService` can be unit-tested
against fabricated run data the way every sibling service is tested against
``FakeFileAdapter``.

Construct it with exactly the artifacts a case needs and leave the rest empty:
absence is the behaviour the runs endpoint exists to report, so "no results at
all" is the DEFAULT here rather than a special setup step. ``sources`` is given
verbatim and is not derived from the other arguments — a test can pin the
"artifact found but it names no entry for this ticket" case, which is a real
on-disk state and a different report from "artifact absent".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
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


class FakeRunArtifactReader:
    """Returns the run artifacts it was constructed with, for any project."""

    def __init__(
        self,
        *,
        sources: Mapping[str, Path | None] | None = None,
        last_stop: LastStop | None = None,
        pr_urls: Mapping[str, str] | None = None,
        results: Mapping[str, RunResultSummary] | None = None,
        receipts: Iterable[str] = (),
    ) -> None:
        self._sources: Mapping[str, Path | None] = (
            sources
            if sources is not None
            else {
                SOURCE_RUN_STATE: None,
                SOURCE_RESULTS: None,
                SOURCE_RECEIPTS: None,
                SOURCE_LAST_STOP: None,
            }
        )
        self._last_stop = last_stop
        self._pr_urls: Mapping[str, str] = pr_urls or {}
        self._results: Mapping[str, RunResultSummary] = results or {}
        self._receipts = frozenset(receipts)

    def source_paths(self, project: Project) -> Mapping[str, Path | None]:
        """Return the configured source paths, ignoring ``project``."""
        return self._sources

    def read_last_stop(self, project: Project) -> LastStop | None:
        """Return the configured :class:`LastStop`, ignoring ``project``."""
        return self._last_stop

    def read_pr_urls(self, project: Project) -> Mapping[str, str]:
        """Return the configured PR urls, ignoring ``project``."""
        return self._pr_urls

    def read_results(
        self, project: Project, ticket_ids: Sequence[str]
    ) -> Mapping[str, RunResultSummary]:
        """Return the configured results for the ids that have one.

        Filtered by ``ticket_ids`` rather than returned whole, so a fake
        configured with a result for an id the manifest does not name behaves like
        the real reader: the record set stays bounded by the manifest.
        """
        return {
            ticket_id: self._results[ticket_id]
            for ticket_id in ticket_ids
            if ticket_id in self._results
        }

    def receipts_present(self, project: Project, ticket_ids: Sequence[str]) -> frozenset[str]:
        """Return the configured receipt ids that appear in ``ticket_ids``."""
        return frozenset(ticket_id for ticket_id in ticket_ids if ticket_id in self._receipts)
