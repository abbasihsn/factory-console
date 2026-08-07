"""The ``GET /api/v1/tickets`` list + ``GET /api/v1/tickets/{ticket_id}`` detail endpoints.

Two workhorse endpoints the SPA's index and detail pages depend on. The list
returns filtered :class:`~factory_console.domain.TicketSummary` records wrapped in
a :class:`TicketListResponse` envelope; the detail returns the full
:class:`~factory_console.domain.Ticket` with its rendered body and resolved
run-state joined in. All request logic lives in
:class:`~factory_console.services.ticket_service.TicketService`, so the handlers
only wire dependencies, load the project, and delegate.

All three roots are the SELECTED project's, resolved per request by
:func:`~factory_console.api.deps.get_current_project_root`, not the one ``create_app``
pinned at boot; in pinned mode the two are the same path. Every filesystem call is
awaited through ``anyio.to_thread.run_sync`` rather than run inline on the event loop —
``ARCHITECTURE.md``'s Cross-cutting **Concurrency** rule, applied per endpoint as it is
touched.

The handlers do no error handling of their own — the app-level handlers
``create_app`` registers cover every failure mode. An invalid ``ticket_id`` is
rejected at the FastAPI ``Path`` boundary against :data:`TICKET_ID_PATTERN` and
re-mapped to the ``invalid_ticket_id`` (400) envelope by the registered
validation handler (so it never reaches the adapter), a
:class:`~factory_console.services.ticket_service.TicketNotFound` raised by the
service propagates to the domain-error handler as the 404 envelope, and the
selection seam's ``no_project_selected``/``selected_project_unavailable`` 409s
propagate the same way.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Annotated

import anyio.to_thread
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from factory_console.api.deps import TicketIdPath, get_current_project_root, get_file_adapter
from factory_console.domain import DepNeighborhood, Ticket, TicketSummary
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.services.deps_service import DepsService
from factory_console.services.ticket_service import TicketService

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the routes and their OpenAPI tag (mirrors ``api/v1/project.py``).
router = APIRouter(tags=["tickets"])


class TicketListResponse(BaseModel):
    """Envelope for the tickets list: the matching summaries and their count."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[TicketSummary]
    total: int


@router.get("/tickets")
async def list_tickets(
    status: Annotated[str | None, Query()] = None,
    track: Annotated[str | None, Query()] = None,
    milestone: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    adapter: FileAdapter = Depends(get_file_adapter),
    root: Path = Depends(get_current_project_root),
) -> TicketListResponse:
    """Return the ticket summaries matching the ``status``/``track``/``milestone``/``q`` filters.

    Loads the SELECTED project at the per-request ``root`` and delegates filtering
    to :class:`TicketService`; ``total`` is the number of matching items (there is
    no pagination in the MVP). Both blocking calls are awaited off the event loop.
    """
    project = await anyio.to_thread.run_sync(partial(adapter.load_project, root))
    items = await anyio.to_thread.run_sync(
        partial(
            TicketService(adapter).list_tickets,
            project,
            status=status,
            track=track,
            milestone=milestone,
            q=q,
        )
    )
    return TicketListResponse(items=items, total=len(items))


@router.get("/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: TicketIdPath,
    adapter: FileAdapter = Depends(get_file_adapter),
    root: Path = Depends(get_current_project_root),
) -> Ticket:
    """Return the full :class:`Ticket` for ``ticket_id`` with run-state resolved.

    ``ticket_id`` is validated at the ``Path`` boundary against the shared
    :data:`TICKET_ID_PATTERN` (an invalid id becomes the ``invalid_ticket_id``
    400 envelope and never reaches the adapter). Loads the SELECTED project at the
    per-request ``root`` and delegates to :class:`TicketService`, whose
    ``TicketNotFound`` propagates to the domain-error handler as a 404 for an id
    absent from the manifest. Both blocking calls are awaited off the event loop.
    """
    project = await anyio.to_thread.run_sync(partial(adapter.load_project, root))
    return await anyio.to_thread.run_sync(
        partial(TicketService(adapter).get_ticket, project, ticket_id)
    )


@router.get("/tickets/{ticket_id}/deps")
async def get_ticket_deps(
    ticket_id: TicketIdPath,
    adapter: FileAdapter = Depends(get_file_adapter),
    root: Path = Depends(get_current_project_root),
) -> DepNeighborhood:
    """Return the :class:`DepNeighborhood` for ``ticket_id``.

    ``ticket_id`` is validated at the ``Path`` boundary against the shared
    :data:`TICKET_ID_PATTERN` (an invalid id becomes the ``invalid_ticket_id``
    400 envelope and never reaches the adapter). Loads the SELECTED project at the
    per-request ``root`` and delegates to :class:`DepsService`, whose
    ``TicketNotFound`` propagates to the domain-error handler as a 404 for an id
    absent from the manifest. Both blocking calls are awaited off the event loop.
    """
    project = await anyio.to_thread.run_sync(partial(adapter.load_project, root))
    return await anyio.to_thread.run_sync(
        partial(DepsService(adapter).get_neighborhood, project, ticket_id)
    )
