"""The ``GET /api/v1/tickets`` list + ``GET /api/v1/tickets/{ticket_id}`` detail endpoints.

Two workhorse endpoints the SPA's index and detail pages depend on. The list
returns filtered :class:`~factory_console.domain.TicketSummary` records wrapped in
a :class:`TicketListResponse` envelope; the detail returns the full
:class:`~factory_console.domain.Ticket` with its rendered body and resolved
run-state joined in. All request logic lives in
:class:`~factory_console.services.ticket_service.TicketService`, so the handlers
only wire dependencies, load the project, and delegate.

The handlers do no error handling of their own — the app-level handlers
``create_app`` registers cover both failure modes. An invalid ``ticket_id`` is
rejected at the FastAPI ``Path`` boundary against :data:`TICKET_ID_PATTERN` and
re-mapped to the ``invalid_ticket_id`` (400) envelope by the registered
validation handler (so it never reaches the adapter), and a
:class:`~factory_console.services.ticket_service.TicketNotFound` raised by the
service propagates to the domain-error handler as the 404 envelope.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict

from factory_console.api.deps import get_file_adapter
from factory_console.domain import DepNeighborhood, Ticket, TicketSummary
from factory_console.domain.ticket import TICKET_ID_PATTERN
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
    request: Request,
    status: Annotated[str | None, Query()] = None,
    track: Annotated[str | None, Query()] = None,
    milestone: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    adapter: FileAdapter = Depends(get_file_adapter),
) -> TicketListResponse:
    """Return the ticket summaries matching the ``status``/``track``/``milestone``/``q`` filters.

    Loads the discovered project from ``request.app.state.project_root`` and
    delegates filtering to :class:`TicketService`; ``total`` is the number of
    matching items (there is no pagination in the MVP).
    """
    root: Path = request.app.state.project_root
    project = adapter.load_project(root)
    items = TicketService(adapter).list_tickets(
        project, status=status, track=track, milestone=milestone, q=q
    )
    return TicketListResponse(items=items, total=len(items))


@router.get("/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: Annotated[str, PathParam(pattern=TICKET_ID_PATTERN)],
    request: Request,
    adapter: FileAdapter = Depends(get_file_adapter),
) -> Ticket:
    """Return the full :class:`Ticket` for ``ticket_id`` with run-state resolved.

    ``ticket_id`` is validated at the ``Path`` boundary against the shared
    :data:`TICKET_ID_PATTERN` (an invalid id becomes the ``invalid_ticket_id``
    400 envelope and never reaches the adapter). Loads the discovered project and
    delegates to :class:`TicketService`, whose ``TicketNotFound`` propagates to the
    domain-error handler as a 404 for an id absent from the manifest.
    """
    root: Path = request.app.state.project_root
    project = adapter.load_project(root)
    return TicketService(adapter).get_ticket(project, ticket_id)


@router.get("/tickets/{ticket_id}/deps")
async def get_ticket_deps(
    ticket_id: Annotated[str, PathParam(pattern=TICKET_ID_PATTERN)],
    request: Request,
    adapter: FileAdapter = Depends(get_file_adapter),
) -> DepNeighborhood:
    """Return the :class:`DepNeighborhood` for ``ticket_id``.

    ``ticket_id`` is validated at the ``Path`` boundary against the shared
    :data:`TICKET_ID_PATTERN` (an invalid id becomes the ``invalid_ticket_id``
    400 envelope and never reaches the adapter). Loads the discovered project and
    delegates to :class:`DepsService`, whose ``TicketNotFound`` propagates to the
    domain-error handler as a 404 for an id absent from the manifest.
    """
    root: Path = request.app.state.project_root
    project = adapter.load_project(root)
    return DepsService(adapter).get_neighborhood(project, ticket_id)
