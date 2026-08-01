"""The run-artifact :class:`RunArtifactReader` port — a sibling of :class:`FileAdapter`.

``ARCHITECTURE.md`` pins :class:`~factory_console.file_adapter.protocol.FileAdapter`
as a fixed eight-method contract that does not cover the factory's ``.factory/``
run artifacts, and ``PROJECT_STRUCTURE.md`` pins the backend track as depending on
"domain models + FileAdapter Protocol" and never on a concrete adapter. Those two
rules only look like they conflict: the way out is not a wider ``FileAdapter``, it
is a NARROW SIBLING PORT — the pattern
:mod:`~factory_console.file_adapter.writer_protocol` already established for the
write path (``FileWriter`` + ``RealFileWriter`` + ``FakeFileWriter`` +
``Depends(get_file_writer)``). This module is that port for the read path's run
artifacts, so :class:`~factory_console.services.run_service.RunService` depends on
an injected abstraction like every other service rather than importing
:mod:`~factory_console.file_adapter.runs` and calling its functions.

The methods are PROJECT-shaped and BATCHED, not path-shaped and per-ticket, and
both halves of that are load-bearing:

- Project-shaped, because a port whose arguments are resolved filesystem paths is
  not an abstraction over the filesystem — a non-filesystem implementation could
  not honour it. Every method takes the resolved
  :class:`~factory_console.domain.project.Project` first, mirroring ``FileAdapter``
  and ``FileWriter``.
- Batched, because the run artifacts live in two directories that are the same for
  every ticket in a request. A per-ticket method forces each implementation to
  re-resolve ``.factory/results`` and ``.factory/receipts`` once per ticket — a
  stat and two ``Path.resolve()`` calls each — turning the list endpoint's
  directory discovery into O(N) redundant syscalls. Asking for the whole
  ticket set at once lets an implementation resolve them once per request.

An id that cannot be safely or successfully read is simply ABSENT from the
returned mapping/set. There is no per-id error channel on purpose: the caller
reports "this source did not answer for this ticket" by naming the source in
:attr:`~factory_console.domain.run_record.RunRecord.unavailable`, and every reason
for silence — no directory, no file, unreadable file, unsafe id — collapses to
that same report.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from factory_console.domain import Project
from factory_console.domain.run_record import LastStop, RunResultSummary, RunSourceName


@runtime_checkable
class RunArtifactReader(Protocol):
    """Read seam between the runs endpoints and the factory's ``.factory/`` artifacts.

    ``@runtime_checkable`` lets tests assert an implementation satisfies the port
    with ``isinstance`` — a structural check on method presence only, not on
    signatures, exactly as :class:`~factory_console.file_adapter.writer_protocol.FileWriter`
    is checked.
    """

    def source_paths(self, project: Project) -> Mapping[RunSourceName, Path | None]:
        """Return each run artifact's absolute path, or ``None`` where it is absent.

        Keyed by :data:`~factory_console.domain.run_record.RunSourceName` — the
        closed key type, so an implementation that omits or misspells one of the
        four is a typing error here rather than a ``KeyError`` in the handler that
        indexes them. Every key is always present; ``None`` is how absence is
        reported. A path is returned only when the implementation would actually
        READ that artifact, so "found" in a response cannot disagree with what was
        read.
        """
        ...

    def read_last_stop(self, project: Project) -> LastStop | None:
        """Return the project's :class:`LastStop`, or ``None`` when the file is absent."""
        ...

    def read_pr_urls(self, project: Project) -> Mapping[str, str]:
        """Return ``{ticket_id: pr_url}`` for the tickets whose run-state names one."""
        ...

    def read_results(
        self, project: Project, ticket_ids: Sequence[str]
    ) -> Mapping[str, RunResultSummary]:
        """Return the lane result for each of ``ticket_ids`` that has a readable one.

        Ids with no result — or that could not be read at all — are absent from
        the mapping rather than present with a ``None`` value.
        """
        ...

    def receipts_present(self, project: Project, ticket_ids: Sequence[str]) -> frozenset[str]:
        """Return the subset of ``ticket_ids`` that have a review receipt.

        PRESENCE ONLY — receipt content is not parsed or modelled anywhere in this
        console (see :class:`~factory_console.domain.run_record.RunRecord`).
        """
        ...
