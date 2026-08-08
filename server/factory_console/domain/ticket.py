"""Ticket domain models and the canonical ticket-id validation pattern.

:data:`TICKET_ID_PATTERN` is the SINGLE source of truth for ticket-id
validation. It is enforced here at the Pydantic model boundary via
:data:`TicketId` and imported verbatim by ``file_adapter/path_safety.py`` and the
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
defense-in-depth in ``file_adapter/path_safety.py``'s ``validate_ticket_id_as_segment``,
which rejects them explicitly, and in ``resolve_ticket_path``, which
resolves the id against the tickets directory and checks containment. Downstream
imports this constant verbatim, so it must not be narrowed here.
"""

TicketId = Annotated[str, StringConstraints(pattern=TICKET_ID_PATTERN)]
"""A ticket id constrained to :data:`TICKET_ID_PATTERN`."""


class TicketContentFields(BaseModel):
    """The five structured fields an App Factory v3 ticket's CONTENT file carries.

    **One definition, both directions.** This is the read projection published on
    :attr:`Ticket.content` AND — by inheritance in
    :mod:`~factory_console.domain.write` — the write surface a client sends. They are
    the same five fields because they mirror the same ``schemas/ticket.schema.json``,
    and a console whose edit form could not send back exactly what it was shown would
    lose a field on every round-trip. It lives here, in the read module, because the
    dependency already runs this way: ``write`` imports ``ticket``, never the reverse.

    **This replaced a single free-text ``bodyMarkdown`` plus an open ``frontMatter``
    dict, and the change is to what a user EDITS, not merely to how it is stored.**
    A v3 ticket is structured, and the schema sets ``additionalProperties: false`` —
    so there is nowhere left to put a paragraph that belongs to no field, and nowhere
    to stash an arbitrary extra key. A console that kept accepting them would be
    accepting input it could not write.

    Constraints mirror that schema, and on the write path they are enforced HERE
    rather than only at render time so a malformed request is a 422 naming the field —
    the requester's error, answered at the boundary — instead of a 500 raised by the
    console's own validation of text it just built. ``criticalFiles`` and
    ``verificationCommands`` carry the schema's ``minItems: 1``, and the schema states
    the reasons: an empty ``critical_files`` silently weakens the overlap filter that
    keeps two lanes off one path, and under INV-42 a ticket declaring no verification
    command can never be verified, only assumed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    context: str = Field(min_length=1)
    approach: str = Field(min_length=1)
    criticalFiles: list[str] = Field(min_length=1)
    interfaceData: str = Field(min_length=1)
    verificationCommands: list[str] = Field(min_length=1)
    verificationNotes: str | None = None


class Ticket(BaseModel):
    """A manifest entry joined with the content file it points at.

    ``raw`` preserves the unmodified manifest entry — including fields this model
    does not name — for forward-compatibility with future factory versions.
    ``extra='forbid'`` governs the model's *own* fields; it does not restrict the
    contents of the ``raw`` mapping. ``runState`` defaults to
    :attr:`RunState.unknown` and is resolved per request by the service on the
    detail path (the manifest/enrichment build sites leave it at the default).

    **``content`` and ``bodyMarkdown`` are the same ticket, and neither replaces the
    other.** ``bodyMarkdown`` is the RENDERED view — what the detail page displays and
    what full-text search indexes — and it exists for both storage formats, which is
    the whole reason a reader can change format without a consumer noticing.
    ``content`` is the structured SOURCE, and it is what an edit form seeds from: you
    cannot offer to edit five fields you were only ever handed a paragraph of.

    ``content`` is therefore ``None`` for a Markdown ticket, and that ``None`` is
    load-bearing rather than a gap. It is the read-side twin of
    :class:`~factory_console.file_adapter.ticket_content.TicketFormatRetired`: a
    ticket with no structured content is exactly a ticket whose edit the write path
    refuses with a 409, so a client that checks it declines to open the form instead
    of presenting five blank boxes whose Save is guaranteed to fail.

    ``files`` stays beside it and is not redundant with ``content.criticalFiles``.
    ``files`` is the format-agnostic DISPLAY projection — a v2 ticket has one from its
    manifest entry and no ``content`` at all — while ``criticalFiles`` is the editable
    field of the content file. They agree for a v3 ticket by construction (see
    :func:`~factory_console.file_adapter.ticket_content.enrich_ticket`), and the day
    they would not is the day one of the two formats stopped answering.
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
    content: TicketContentFields | None = None
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
