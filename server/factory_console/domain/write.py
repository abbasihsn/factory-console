"""Write-path domain models — the canonical Pydantic v2 write DTOs.

Every v2 write flows through this small set of request/response types so the
FileWriter port, the diff engine, and the backend endpoints reference the SAME
shapes (the read-side pattern: domain models double as the shared-types
contract). Pure models, no I/O — the foundation the rest of the file-adapter
track builds on. This is the single source of the write DTOs; the API endpoints
consume these directly rather than defining a parallel ``api/v1/write_models.py``.

Ticket-id validation reuses :data:`TicketId` / :data:`TICKET_ID_PATTERN` from
:mod:`factory_console.domain.ticket` verbatim — the regex is defined in exactly
one place. Field names are camelCase per the REST v1 contract so these models
serialize directly to the SPA's shared types.

:class:`~factory_console.domain.ticket.TicketContentFields` is imported for the
same reason and re-exported here because this is where write-path callers look for
it. The five content fields are one shape read and written, not two that happen to
match: the read model publishes them on ``Ticket.content`` and the edit form sends
the same five back, so defining them twice is how a round-trip comes to lose one.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from factory_console.domain.ticket import (
    TICKET_ID_PATTERN,
    Ticket,
    TicketContentFields,
    TicketId,
)

__all__ = [
    "TICKET_ID_PATTERN",
    "TicketContentFields",
    "TicketDraft",
    "TicketEdit",
    "FileDiff",
    "DiffPreview",
    "WriteResult",
]


class TicketDraft(TicketContentFields):
    """Inbound request body to CREATE a ticket.

    ``id`` is validated against :data:`TICKET_ID_PATTERN` at this boundary; the rest
    split cleanly in two, matching where v3 stores them. ``title`` / ``track`` /
    ``milestone`` / ``dependsOn`` / ``provides`` are INDEX fields and land in
    ``tickets.json``; the inherited five are CONTENT fields and land in the ticket's
    own file. Nothing here is written to both, which is what keeps the two from
    disagreeing.

    ``files`` is gone from this surface: v3's index carries no such key, and the
    content file's ``criticalFiles`` is the same information stored where the factory
    reads it from.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: TicketId
    title: str
    track: str | None = None
    milestone: str | None = None
    dependsOn: list[str] = Field(default_factory=list)
    provides: str = ""


class TicketEdit(TicketContentFields):
    """Inbound request body to EDIT a ticket.

    Identical to :class:`TicketDraft` minus ``id`` — the id of the ticket being
    edited comes from the path parameter, not the body.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    track: str | None = None
    milestone: str | None = None
    dependsOn: list[str] = Field(default_factory=list)
    provides: str = ""


class FileDiff(BaseModel):
    """A single file's planned or applied change, as a unified diff.

    ``path`` is project-relative POSIX; ``changeKind`` classifies the change;
    ``diff`` is the unified-diff text the SPA renders.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    changeKind: Literal["create", "modify", "delete"]
    diff: str


class DiffPreview(BaseModel):
    """The set of per-file diffs a write produces, keyed by ticket id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticketId: str
    files: list[FileDiff] = Field(default_factory=list)


class WriteResult(BaseModel):
    """Uniform envelope returned by every write endpoint (apply or dry-run).

    Two shapes:

    * **Apply** → ``applied=True``, ``ticket`` set to the re-read
      :class:`~factory_console.domain.ticket.Ticket`, ``changedFiles`` the paths
      actually written, ``diff`` the preview of what was written.
    * **Dry-run** → ``applied=False``, ``ticket=None``, ``changedFiles`` the
      planned paths, ``diff`` the preview of what would be written.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    applied: bool
    ticketId: str
    changedFiles: list[str] = Field(default_factory=list)
    diff: DiffPreview
    ticket: Ticket | None = None

    @model_validator(mode="after")
    def _ticket_matches_applied(self) -> WriteResult:
        """Enforce the two-shape contract: ``ticket`` is set iff ``applied``.

        An apply carries the re-read ticket; a dry-run carries none. Making the
        invariant a validator (not just a docstring) stops an apply-path handler
        from emitting ``applied=True`` with ``ticket=None`` (or the reverse), which
        the SPA — relying on this shape — would mishandle.
        """
        if (self.ticket is not None) != self.applied:
            raise ValueError("ticket must be set iff applied is True")
        return self
