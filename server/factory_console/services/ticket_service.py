"""Ticket list + detail application service.

:class:`TicketService` holds the request logic for the two ticket endpoints so
the HTTP handlers stay thin: it filters the adapter's summaries for the list view
and joins the resolved run-state onto the full ticket for the detail view. It
owns :class:`TicketNotFound` — the domain error the detail path raises when an id
is absent from the manifest — co-located here per the ``errors.py`` convention
that a :class:`~factory_console.errors.FactoryConsoleError` subclass lives where
it is raised. The service depends only on the read-only
:class:`~factory_console.file_adapter.protocol.FileAdapter` port, never on a
concrete adapter or the filesystem.
"""

from __future__ import annotations

from factory_console.domain import Project, Ticket, TicketSummary
from factory_console.errors import FactoryConsoleError
from factory_console.file_adapter.protocol import FileAdapter


class TicketNotFound(FactoryConsoleError):
    """Raised when a ticket id is absent from the target project's manifest.

    Carries the ``ticket_not_found`` code at HTTP 404; the app-level domain-error
    handler renders it to the REST v1 envelope, so the detail handler never
    catches it.
    """

    def __init__(self, ticket_id: str) -> None:
        super().__init__(
            code="ticket_not_found",
            message=f"Ticket {ticket_id!r} not found",
            status=404,
            details=None,
        )


class TicketService:
    """Filters ticket summaries and resolves ticket detail over a ``FileAdapter``.

    Constructed per request with the injected adapter; holds no state beyond it.
    """

    def __init__(self, adapter: FileAdapter) -> None:
        self._adapter = adapter

    def list_tickets(
        self,
        project: Project,
        *,
        status: str | None,
        track: str | None,
        milestone: str | None,
        q: str | None,
    ) -> list[TicketSummary]:
        """Return the project's ticket summaries matching every provided filter.

        ``adapter.list_tickets`` already resolves each summary's ``runState`` (a
        seeded-map lookup in the fake, an on-disk probe in the real adapter via
        the shared projection), so this method only filters — it never re-probes
        run-state. ``status`` / ``track`` / ``milestone`` are exact-equality
        filters applied only when the argument is a non-empty string; ``q`` (when
        truthy) is a case-insensitive substring match over the ticket id AND
        title. A blank value (``""``, how FastAPI parses ``?status=`` — not
        ``None``) is treated as "unset" for ALL four params, so an empty query
        param means "no filter" rather than "match only the empty string" (which
        would silently return zero tickets). Multiple filters combine with AND and
        input order is preserved.
        """
        summaries = self._adapter.list_tickets(project)
        needle = q.lower() if q else None
        return [
            summary
            for summary in summaries
            if (not status or summary.status == status)
            and (not track or summary.track == track)
            and (not milestone or summary.milestone == milestone)
            and (needle is None or needle in summary.id.lower() or needle in summary.title.lower())
        ]

    def get_ticket(self, project: Project, ticket_id: str) -> Ticket:
        """Return the full :class:`Ticket` for ``ticket_id`` with run-state joined in.

        Raises :class:`TicketNotFound` when the adapter has no ticket for the id.
        The adapter leaves the returned ticket's ``runState`` at its default on
        this path, so we resolve it here via ``read_run_state`` and copy it in.
        This is a deliberate asymmetry with :meth:`list_tickets`, whose summaries
        arrive already run-state-resolved: run-state is joined on BOTH paths, but
        only the detail path probes (do not "fix" this into a redundant re-probe
        on the list path).
        """
        ticket = self._adapter.get_ticket(project, ticket_id)
        if ticket is None:
            raise TicketNotFound(ticket_id)
        return ticket.model_copy(
            update={"runState": self._adapter.read_run_state(project, ticket_id)}
        )
