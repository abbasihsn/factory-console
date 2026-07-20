"""Ticket domain models and the ticket-id validation pattern.

:data:`TICKET_ID_PATTERN` is the SINGLE source of truth for ticket-id
validation (path-traversal defense); file-adapter modules and backend path
params import it verbatim. Mirrors the ``Ticket`` / ``TicketSummary`` entries
of ``ARCHITECTURE.md`` data_model. No I/O here.
"""

from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from factory_console.domain.run_state import RunState

TICKET_ID_PATTERN = r"^[A-Za-z0-9_.-]+$"
"""Single source of truth for ticket-id validation (path-traversal defense)."""

TicketId = Annotated[str, StringConstraints(pattern=TICKET_ID_PATTERN)]


class Ticket(BaseModel):
    """A ticket: a manifest entry joined with its ``.md`` file.

    ``raw`` preserves the unknown/forward-compat fields of the manifest entry;
    ``extra="forbid"`` rejects unknown top-level fields but places no
    constraint on the contents of ``raw``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: TicketId
    title: str
    status: str
    track: str | None = None
    milestone: str | None = None
    dependsOn: list[str] = Field(default_factory=list)
    provides: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    filePath: Path
    bodyMarkdown: str
    bodyHtml: str
    raw: dict[str, Any] = Field(default_factory=dict)


class TicketSummary(BaseModel):
    """List projection of a :class:`Ticket` with resolved run-state and dep counts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: TicketId
    title: str
    status: str
    track: str | None = None
    milestone: str | None = None
    runState: RunState
    depCount: int
    dependentCount: int
