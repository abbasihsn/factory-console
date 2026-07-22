"""Ticket dependency-neighborhood application service.

:class:`DepsService` holds the request logic for the ``/tickets/{id}/deps``
endpoint so the HTTP handler stays thin: it delegates to the adapter's
``get_deps`` and translates the absent-id sentinel into the domain error. It is
the structural twin of
:class:`~factory_console.services.ticket_service.TicketService.get_ticket` —
delegate, then map ``None`` to :class:`TicketNotFound` — and depends only on the
read-only :class:`~factory_console.file_adapter.protocol.FileAdapter` port.

Two deliberate omissions a future reader should NOT "fix" back in:

* **No fallback composition.** ``get_deps`` is a REQUIRED method on the
  ``FileAdapter`` Protocol (present on every adapter), and the neighborhood is
  built in exactly ONE place —
  :meth:`~factory_console.file_adapter.projection.TicketProjection.neighborhood`.
  Re-deriving ``directDeps`` / ``directDependents`` / ``unresolvedDeps`` here (a
  fetch-all + index + reverse-scan) would duplicate that projection, drift from
  it, and be dead, untestable code. The service is a pure delegate-then-raise
  wrapper by design.
* **No run-state join.** Unlike the detail path, where the service must probe and
  copy run-state onto the ticket, ``get_deps`` already returns a fully
  run-state-resolved neighborhood (every summary is projected through the shared
  ``run_state_for``), so there is nothing left to join here.

:class:`TicketNotFound` is REUSED from
:mod:`factory_console.services.ticket_service` (the T22-owned exception) rather
than redefined, so the absent-ticket contract is 404 with the same
``ticket_not_found`` code across both read paths.
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

        Delegates to ``adapter.get_deps``, which builds the neighborhood via the
        shared projection with run-state already resolved on every summary — so
        this method adds no run-state join and no fallback composition (see the
        module docstring for why). Raises :class:`TicketNotFound` when the adapter
        has no ticket for the id (``get_deps`` returns ``None``); the app-level
        domain-error handler renders that to the 404 envelope.
        """
        neighborhood = self._adapter.get_deps(project, ticket_id)
        if neighborhood is None:
            raise TicketNotFound(ticket_id)
        return neighborhood
