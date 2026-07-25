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
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from factory_console.domain.ticket import TICKET_ID_PATTERN, Ticket, TicketId

__all__ = [
    "TICKET_ID_PATTERN",
    "TicketDraft",
    "TicketEdit",
    "FileDiff",
    "DiffPreview",
    "WriteResult",
]


class TicketDraft(BaseModel):
    """Inbound request body to CREATE a ticket.

    ``id`` is validated against :data:`TICKET_ID_PATTERN` at this boundary; the
    rest mirror the manifest/body fields a new ticket carries. ``frontMatter``
    holds arbitrary extra YAML front-matter keys not named explicitly.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: TicketId
    title: str
    track: str | None = None
    milestone: str | None = None
    dependsOn: list[str] = Field(default_factory=list)
    provides: str = ""
    files: list[str] = Field(default_factory=list)
    bodyMarkdown: str
    frontMatter: dict[str, Any] = Field(default_factory=dict)


class TicketEdit(BaseModel):
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
    files: list[str] = Field(default_factory=list)
    bodyMarkdown: str
    frontMatter: dict[str, Any] = Field(default_factory=dict)


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
