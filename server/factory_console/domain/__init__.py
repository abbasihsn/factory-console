"""Shared Pydantic v2 domain models for the factory console.

The names re-exported here are the stable type contract every other track builds
on — the file-adapter, the backend, and (transitively, via OpenAPI) the frontend
generated types. :data:`TICKET_ID_PATTERN` is the single source of truth for
ticket-id validation and is re-exported so downstream code imports it verbatim.
"""

from __future__ import annotations

from factory_console.domain.deps import DepNeighborhood, Roadmap
from factory_console.domain.project import Project
from factory_console.domain.registry import (
    REGISTERED_PROJECT_ID_PATTERN,
    RegisteredProject,
    RegistryEntry,
    RegistryEntryCondition,
)
from factory_console.domain.run_record import RunRecord
from factory_console.domain.run_state import RunState
from factory_console.domain.run_state_source import (
    RUN_STATE_SOURCE_LOCATIONS,
    JsonRunState,
    RunStateSource,
    RunStateSourceKind,
)
from factory_console.domain.ticket import (
    TICKET_ID_PATTERN,
    Ticket,
    TicketId,
    TicketSummary,
)

__all__ = [
    "REGISTERED_PROJECT_ID_PATTERN",
    "RUN_STATE_SOURCE_LOCATIONS",
    "TICKET_ID_PATTERN",
    "DepNeighborhood",
    "JsonRunState",
    "Project",
    "RegisteredProject",
    "RegistryEntry",
    "RegistryEntryCondition",
    "Roadmap",
    "RunRecord",
    "RunState",
    "RunStateSource",
    "RunStateSourceKind",
    "Ticket",
    "TicketId",
    "TicketSummary",
]
