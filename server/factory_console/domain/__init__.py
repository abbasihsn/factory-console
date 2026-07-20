"""Public Pydantic v2 domain models shared across the file-adapter and backend.

Re-exports the domain entities and :data:`TICKET_ID_PATTERN` (the single source
of truth for ticket-id validation).
"""

from factory_console.domain.deps import DepNeighborhood, Roadmap
from factory_console.domain.project import Project
from factory_console.domain.run_state import RunState
from factory_console.domain.ticket import (
    TICKET_ID_PATTERN,
    Ticket,
    TicketId,
    TicketSummary,
)

__all__ = [
    "TICKET_ID_PATTERN",
    "DepNeighborhood",
    "Project",
    "Roadmap",
    "RunState",
    "Ticket",
    "TicketId",
    "TicketSummary",
]
