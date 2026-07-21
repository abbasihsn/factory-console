"""Shared path-safety exception for ticket ids used as filesystem path segments.

Both file-adapter modules that turn a ticket id into a filesystem path —
:mod:`~factory_console.file_adapter.ticket_md` and
:mod:`~factory_console.file_adapter.run_state` — raise the SAME
:class:`PathTraversal` from here, so the REST error code is uniform
(``invalid_ticket_id``, per ARCHITECTURE.md's run-state/id contract) and a single
``except PathTraversal`` at the edge layer catches every unsafe-id path rather
than only one module's copy.
"""

from __future__ import annotations

from factory_console.domain.ticket import TICKET_ID_PATTERN
from factory_console.errors import FactoryConsoleError

_DEFAULT_REASON = "ticket id failed path-safety validation"
_PATTERN_VIOLATION_REASON = f"Ticket id must match {TICKET_ID_PATTERN}"


class PathTraversal(FactoryConsoleError):
    """A ticket id is unsafe to resolve to a filesystem path.

    Raised when an id violates :data:`~factory_console.domain.ticket.TICKET_ID_PATTERN`,
    is a bare ``.``/``..`` segment, or resolves outside the project root. Every
    cause maps to the SAME transport contract (status 400, ``invalid_ticket_id``)
    so the edge layer rejects unsafe ids uniformly. ``details`` echoes only the
    (already user-supplied) ``ticketId`` — never a resolved absolute path, which
    would disclose the server's filesystem layout. ``reason`` sets the
    human-readable message; the stable ``code`` never varies.
    """

    def __init__(self, ticket_id: str, *, reason: str = _DEFAULT_REASON) -> None:
        super().__init__(
            code="invalid_ticket_id",
            message=reason,
            status=400,
            details={"ticketId": ticket_id},
        )

    @classmethod
    def from_pattern_violation(cls, ticket_id: str) -> PathTraversal:
        """Build the ``invalid_ticket_id`` error for an id that fails the id pattern.

        The single owner of the pattern-violation message, shared by the
        file-adapter re-validation (:mod:`~factory_console.file_adapter.ticket_md`)
        and the HTTP ``Path``-boundary rejection
        (:mod:`~factory_console.api.error_handlers`), so the ``invalid_ticket_id``
        envelope is identical whether an id is rejected at the edge or deeper in
        resolution — the message is defined here once, never restated at a call site.
        """
        return cls(ticket_id, reason=_PATTERN_VIOLATION_REASON)
