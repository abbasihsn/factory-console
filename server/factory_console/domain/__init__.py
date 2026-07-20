"""Shared Pydantic v2 domain models for the factory console.

The names re-exported here are the stable type contract every other track builds
on — the file-adapter, the backend, and (transitively, via OpenAPI) the frontend
generated types. :data:`TICKET_ID_PATTERN` is the single source of truth for
ticket-id validation and is re-exported so downstream code imports it verbatim.
"""

from __future__ import annotations

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
