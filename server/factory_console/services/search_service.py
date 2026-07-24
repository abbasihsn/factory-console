"""Cross-ticket full-text search application service.

:class:`SearchService` holds the request logic for the ``GET /api/v1/search``
endpoint so the HTTP handler stays thin: it normalizes the raw query and
delegates the actual ranking to the adapter's ``search_tickets`` — the T36
file-adapter capability that reads ticket BODIES, not just id+title (the
distinction from T22's list ``?q=`` substring filter). The service depends only
on the read-only :class:`~factory_console.file_adapter.protocol.FileAdapter`
port, never on a concrete adapter or the filesystem, mirroring
:class:`~factory_console.services.ticket_service.TicketService` and
:class:`~factory_console.services.deps_service.DepsService`.
"""

from __future__ import annotations

from factory_console.domain import Project
from factory_console.domain.search import SearchHit
from factory_console.file_adapter.protocol import FileAdapter


class SearchService:
    """Ranks tickets by a full-text query over a ``FileAdapter``.

    Constructed per request with the injected adapter; holds no state beyond it.
    """

    def __init__(self, adapter: FileAdapter) -> None:
        self._adapter = adapter

    def search(self, project: Project, query: str, *, limit: int) -> list[SearchHit]:
        """Return the best ``limit`` :class:`SearchHit` s for ``query``, best first.

        Normalizes the query by stripping surrounding whitespace, then
        short-circuits a blank or whitespace-only query to ``[]`` WITHOUT
        consulting the adapter — the underlying ``rank_tickets`` already treats a
        blank query as no-match, but the service still guards it so a blank ``q``
        never reaches the port. Otherwise delegates to ``adapter.search_tickets``,
        which ranks id/title/``provides``/body and truncates to the first
        ``limit`` hits. Does no filesystem access.
        """
        stripped = query.strip()
        if not stripped:
            return []
        return self._adapter.search_tickets(project, stripped, limit=limit)
