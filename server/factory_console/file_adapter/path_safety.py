"""Shared path-safety for ticket ids used as filesystem path segments.

Every file-adapter module that turns a ticket id into a filesystem path raises the
SAME :class:`PathTraversal` from here, so the REST error code is uniform
(``invalid_ticket_id``, per ARCHITECTURE.md's run-state/id contract) and a single
``except PathTraversal`` at the edge layer catches every unsafe-id path rather
than only one module's copy. Deliberately NOT an enumeration of those modules: an
earlier revision listed them by name, the list went stale the first time a fourth
module joined, and a maintainer reading a stale list as exhaustive is exactly how
the rule this module centralises drifts back apart.

:func:`validate_ticket_id_as_segment` is the shared implementation of the
segment rule itself, for the same reason the exception is shared — see its
docstring.
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


def validate_ticket_id_as_segment(ticket_id: str) -> None:
    """Raise :class:`PathTraversal` unless ``ticket_id`` is one path-safe segment.

    Defense-in-depth for every module that joins a ticket id onto a filesystem
    path: the id was already validated at the API boundary, but a boundary check
    protects only the callers that went through the boundary, so it is re-validated
    at the point of use.

    Two rules, and the second is not implied by the first. ``fullmatch`` (not
    ``match``) so a trailing newline cannot sneak past the ``$`` anchor. And
    :data:`TICKET_ID_PATTERN` admits ``.`` as an ordinary character, so bare ``.``
    and ``..`` satisfy the regex while still being single-segment traversals —
    they are rejected explicitly, per the ARCHITECTURE run-state directory
    contract.

    It lives HERE, beside the exception it raises, because the rule and the error
    contract are one decision. It was previously copied per module, and a copied
    rule is one a future tightening applies to some call sites and not others —
    leaving an id that :mod:`~factory_console.file_adapter.run_state` refuses and a
    sibling reader accepts, which is a path-safety difference no test would think
    to look for.
    """
    if re.fullmatch(TICKET_ID_PATTERN, ticket_id) is None or ticket_id in (".", ".."):
        raise PathTraversal(ticket_id)
