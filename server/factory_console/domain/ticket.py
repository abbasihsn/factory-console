"""Ticket domain models and the canonical ticket-id validation pattern.

:data:`TICKET_ID_PATTERN` is the SINGLE source of truth for ticket-id
validation. It is enforced here at the Pydantic model boundary via
:data:`TicketId` and imported verbatim by ``file_adapter/ticket_md.py`` and the
backend's path params so the constraint is defined in exactly one place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from factory_console.domain.run_state import RunState

TICKET_ID_PATTERN = r"^[A-Za-z0-9_.-]+$"
"""Canonical ticket-id regex — the single source of truth for id validation.

Allows letters, digits, ``_``, ``.`` and ``-`` only; because it excludes path
separators (``/``, ``\\``) and whitespace it blocks the primary path-traversal
vectors at the type layer. Note it does *not* by itself reject a bare ``.`` or
``..`` (dots are allowed characters) — the dot-dot traversal guard is
defense-in-depth in ``file_adapter/ticket_md.py``'s ``_safe_resolve``, which
resolves the id against the tickets directory and checks containment. Downstream
imports this constant verbatim, so it must not be narrowed here.
"""

TicketId = Annotated[str, StringConstraints(pattern=TICKET_ID_PATTERN)]
"""A ticket id constrained to :data:`TICKET_ID_PATTERN`."""


class Ticket(BaseModel):
    """A manifest entry joined with its rendered ``.md`` body.

    ``raw`` preserves the unmodified manifest entry — including fields this model
    does not name — for forward-compatibility with future factory versions.
    ``extra='forbid'`` governs the model's *own* fields; it does not restrict the
    contents of the ``raw`` mapping. ``runState`` defaults to
    :attr:`RunState.unknown` and is resolved per request by the service on the
    detail path (the manifest/enrichment build sites leave it at the default).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: TicketId
    title: str
    status: str
    track: str | None = None
    milestone: str | None = None
    runState: RunState = RunState.unknown
    dependsOn: list[str] = Field(default_factory=list)
    provides: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    filePath: Path
    bodyMarkdown: str
    bodyHtml: str
    raw: dict[str, Any]


class TicketSummary(BaseModel):
    """List-projection of a ticket for the tickets index view.

    ``runState`` is resolved per request by probing the factory run-state
    directory; ``depCount`` / ``dependentCount`` come from reverse-indexing
    ``dependsOn`` across the manifest.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: TicketId
    title: str
    status: str
    track: str | None = None
    milestone: str | None = None
    runState: RunState
    depCount: int
    dependentCount: int
