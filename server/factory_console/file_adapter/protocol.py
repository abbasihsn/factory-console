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
        """Return the :class:`RunState` for ``ticket_id`` (``unknown`` when undetermined)."""
        ...

    def get_roadmap(self, project: Project) -> Roadmap | None:
        """Return the project :class:`Roadmap`, or ``None`` when the project has none."""
        ...
