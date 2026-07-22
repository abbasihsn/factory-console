"""Dependency-neighborhood application service.

:class:`DepsService` holds the request logic for the ticket deps endpoint so the
HTTP handler stays thin: it asks the adapter for a ticket's
:class:`~factory_console.domain.DepNeighborhood` and raises
:class:`~factory_console.services.ticket_service.TicketNotFound` when the id is
absent. It reuses that T22-owned domain error rather than defining its own, and
delegates neighborhood-building straight to the port — the resolved deps,
dependents, and unresolved-id derivation all live in the one shared
:class:`~factory_console.file_adapter.projection.TicketProjection` that backs both
the list and dependency views, so the service adds no second copy of that logic.
The service depends only on the read-only
:class:`~factory_console.file_adapter.protocol.FileAdapter` port, never on a
concrete adapter or the filesystem.
"""

from __future__ import annotations

from factory_console.domain import DepNeighborhood, Project
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.services.ticket_service import TicketNotFound


class DepsService:
    """Resolves a ticket's dependency neighborhood over a ``FileAdapter``.

    Constructed per request with the injected adapter; holds no state beyond it.
    """

    def __init__(self, adapter: FileAdapter) -> None:
        self._adapter = adapter

    def get_neighborhood(self, project: Project, ticket_id: str) -> DepNeighborhood:
        """Return the :class:`DepNeighborhood` for ``ticket_id``.

        Delegates directly to ``adapter.get_deps`` — a first-class method on the
        ``FileAdapter`` port whose neighborhood-building is centralized in the
        shared :class:`~factory_console.file_adapter.projection.TicketProjection`,
        so the list and dependency views cannot drift. Raises
        :class:`TicketNotFound` when the adapter has no ticket for the id (the
        only case ``get_deps`` returns ``None``), mirroring
        :meth:`~factory_console.services.ticket_service.TicketService.get_ticket`.
        """
        neighborhood = self._adapter.get_deps(project, ticket_id)
        if neighborhood is None:
            raise TicketNotFound(ticket_id)
        return neighborhood
