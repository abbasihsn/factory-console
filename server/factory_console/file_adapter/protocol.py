"""The read-only :class:`FileAdapter` port.

:class:`FileAdapter` is the internal seam between the HTTP handlers and
filesystem I/O: handlers depend on this ``Protocol`` (wired via
``FastAPI.Depends()``) and never call ``open()`` directly. Two implementations
satisfy it structurally — a filesystem-backed ``RealFileAdapter`` and the
in-memory :class:`~factory_console.file_adapter.fake.FakeFileAdapter` used by
tests. The port is deliberately read-only: the console never writes to the
target project.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from factory_console.domain import (
    DepNeighborhood,
    Project,
    Roadmap,
    RunState,
    Ticket,
    TicketSummary,
)
from factory_console.domain.graph import TicketGraph
from factory_console.domain.search import SearchHit


@runtime_checkable
class FileAdapter(Protocol):
    """Read-only seam between HTTP handlers and filesystem I/O.

    Every method except :meth:`load_project` takes the resolved
    :class:`~factory_console.domain.project.Project` for the request and returns
    read-through domain entities; an implementation must not mutate the project
    or write to the target filesystem. ``@runtime_checkable`` lets tests assert
    an implementation satisfies the port with ``isinstance`` — a structural
    check on method presence only, not on signatures.
    """

    def load_project(self, root: Path) -> Project:
        """Resolve the target project rooted at ``root`` and return its :class:`Project`."""
        ...

    def list_tickets(self, project: Project) -> list[TicketSummary]:
        """Project every ticket to a :class:`TicketSummary` with run-state and edge counts."""
        ...

    def get_ticket(self, project: Project, ticket_id: str) -> Ticket | None:
        """Return the full :class:`Ticket` for ``ticket_id``, or ``None`` if absent."""
        ...

    def get_deps(self, project: Project, ticket_id: str) -> DepNeighborhood | None:
        """Return the :class:`DepNeighborhood` for ``ticket_id``, or ``None`` if absent."""
        ...

    def read_run_state(self, project: Project, ticket_id: str) -> RunState:
        """Return the :class:`RunState` for ``ticket_id``.

        ``unknown`` when there is no run-state source to ask or it could not be
        trusted; ``absent`` when a source resolved and does not list the id. The two
        are NOT interchangeable at the write gate — ``unknown`` is mutable, ``absent``
        is refused 409 (T80).
        """
        ...

    def get_roadmap(self, project: Project) -> Roadmap | None:
        """Return the project :class:`Roadmap`, or ``None`` when the project has none."""
        ...

    def search_tickets(
        self, project: Project, query: str, *, limit: int | None = None
    ) -> list[SearchHit]:
        """Rank tickets by ``query`` over id/title/``provides``/body, best first.

        Returns a :class:`~factory_console.domain.search.SearchHit` per matching
        ticket, ordered by descending relevance score; a blank or whitespace-only
        query returns ``[]``. ``limit`` truncates to the first ``limit`` hits when
        not ``None``.
        """
        ...

    def get_graph(self, project: Project) -> TicketGraph:
        """Project the whole ticket set to the run-state-coloured dependency DAG.

        Returns a :class:`~factory_console.domain.graph.TicketGraph`: one node per
        ticket (carrying the same run-state as :meth:`list_tickets`) and one edge
        per RESOLVED ``dependsOn`` relation — self-loops dropped, dangling ids
        omitted, duplicates collapsed.
        """
        ...
