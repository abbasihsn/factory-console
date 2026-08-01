"""Shared path-safety rule and exception for ticket ids used as path segments.

Every file-adapter module that turns a ticket id into a filesystem path raises the
SAME :class:`PathTraversal` from here, so the REST error code is uniform
(``invalid_ticket_id``, per ARCHITECTURE.md's run-state/id contract) and a single
``except PathTraversal`` at the edge layer catches every unsafe-id path rather
than only one module's copy.

The SEGMENT rule lives here too, in :func:`require_safe_ticket_id_segment` — not
just the exception — for the run-state read path:
:mod:`~factory_console.file_adapter.run_state` and
:mod:`~factory_console.file_adapter.runs` both call it instead of each restating
"fullmatch the pattern, then reject bare ``.``/``..``" inline. Two copies of a
safety rule is one too many; a newly-disallowed segment must not need editing in
two places to hold.

NOT yet every id-to-path module. :mod:`~factory_console.file_adapter.ticket_md`,
:mod:`~factory_console.file_adapter.write_render` and
:mod:`~factory_console.file_adapter.fake_writer` still check only the PATTERN
inline, so a bare ``.``/``..`` — which the pattern admits — reaches their join as
the contained-but-nonsense filename ``..md`` and surfaces as a 404 "no ticket
file" where the modules above return a 400 "unsafe id". That divergence is real
and predates this function; converting those three changes write-endpoint status
codes, which is a separate ticket's call to make, not this one's.

A caller that builds a path still owes a second, different check — that the join
RESOLVES inside the project root (``ticket_md``/``write_render``'s containment
check, ``runs``'s ``_probe``). This function bounds the SEGMENT; it cannot bound
what a symlink does with it.
"""

from __future__ import annotations

import re

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


def require_safe_ticket_id_segment(ticket_id: str) -> str:
    """Return ``ticket_id`` if it is safe to use as ONE path segment, else raise.

    The single owner of the segment-safety rule. ``fullmatch`` (not ``match``) so
    a trailing newline cannot slip past the ``$`` anchor, plus an explicit
    rejection of bare ``.``/``..``, which satisfy
    :data:`~factory_console.domain.ticket.TICKET_ID_PATTERN` (it allows ``.`` as
    a character) yet are single-segment traversals.

    Callers use this for defense-in-depth: an id reaching a file adapter was
    already validated at the API boundary, but it is about to become a path
    segment, so it is re-validated BEFORE any path is joined or probed.

    Raises:
        PathTraversal: (``invalid_ticket_id``, 400) before any path is joined.
    """
    if re.fullmatch(TICKET_ID_PATTERN, ticket_id) is None:
        raise PathTraversal.from_pattern_violation(ticket_id)
    if ticket_id in (".", ".."):
        raise PathTraversal(ticket_id)
    return ticket_id
