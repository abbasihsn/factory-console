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

:func:`resolve_or_none` and :func:`within_root` are the CONTAINMENT half of the
same subject, and live here for the same reason again. Every module that turns a
ticket id into a path also has to ask "and does the resolved path stay under the
project root?", and that question was answered by a private copy per module. The
copies are not equivalent: the one here refuses to raise on a resolution that
itself fails, which the older inline ``.resolve(strict=False)`` calls in
:mod:`~factory_console.file_adapter.ticket_md` and
:mod:`~factory_console.file_adapter.write_render` do not — see
:func:`resolve_or_none`. Those two still carry their own; this module is where the
hardened rule now lives so that converging them is a small edit rather than a
rewrite.
"""

from __future__ import annotations

import re
from pathlib import Path

from factory_console.domain.ticket import TICKET_ID_PATTERN
from factory_console.errors import FactoryConsoleError

_DEFAULT_REASON = "ticket id failed path-safety validation"
_PATTERN_VIOLATION_REASON = f"Ticket id must match {TICKET_ID_PATTERN}"
_ROOT_ESCAPE_REASON = "Ticket id resolves outside the project root"


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

    @classmethod
    def from_root_escape(cls, ticket_id: str) -> PathTraversal:
        """Build the ``invalid_ticket_id`` error for a path that escapes the root.

        The containment refusal's counterpart to :meth:`from_pattern_violation`, and
        here for the same reason: the message was a private ``_ID_ESCAPES_ROOT``
        constant copied into each module that performs the check, with nothing but a
        comment asserting the copies stay word-identical. A reword in one of them would
        silently split the ``invalid_ticket_id`` envelope across endpoints — which is
        exactly what the sibling classmethod exists to prevent for the other message.
        """
        return cls(ticket_id, reason=_ROOT_ESCAPE_REASON)


def resolve_or_none(path: Path) -> Path | None:
    """``path.resolve(strict=False)``, or ``None`` when the resolution itself fails.

    :meth:`Path.resolve` is NOT total, and non-strict does not mean non-raising.
    Through CPython 3.12 it re-stats the resolved path and converts ``ELOOP`` into
    a ``RuntimeError("Symlink loop from ...")``; 3.13 dropped that re-stat and
    answers with the unresolved path instead. ``pyproject.toml`` declares
    ``requires-python = ">=3.11"`` with no upper bound, so BOTH behaviours are
    inside the supported range — the same interpreter-drift trap
    :func:`~factory_console.file_adapter.run_state._node_exists` exists to
    neutralise, met here one layer earlier. Left unhandled, a symlink loop on a path a
    reader was asked for escapes as a ``RuntimeError`` on 3.11/3.12 — an unmapped 500
    from a module that documents raising nothing but :class:`PathTraversal` — while
    3.13 answers the same bytes on disk cleanly. ``ValueError`` joins it for a path that
    cannot be encoded (an embedded NUL), matching the ``except ValueError`` the
    run-state probes carry.

    ``None`` means the containment question cannot be ANSWERED, which is not the same
    as answering it NO — so callers must treat it as "I could not look" and never read
    the path, rather than as a traversal to refuse.
    """
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def within_root(resolved: Path, project_root: Path) -> bool | None:
    """Is ``resolved`` contained under ``project_root``? ``None`` if undecidable.

    The containment gate no id validation can cover: a symlinked directory or file
    under the project resolves wherever it points, and a reader must not read through
    it. The ROOT is resolved too, so a symlinked root (``/tmp`` on some platforms) is
    not a false escape.

    The single containment implementation for every caller, so no two readers can
    answer this differently — they may differ only in how they PHRASE a refusal, never
    in what counts as one.

    ``None`` (the root itself would not resolve) is deliberately not ``False``: a
    question that could not be put has not been answered NO. Both are refusals, but
    only ``False`` is a proven escape, and only a proven escape may be reported as one.
    """
    root = resolve_or_none(project_root)
    if root is None:
        return None
    return resolved.is_relative_to(root)


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

    The two rules raise DIFFERENT messages under the one ``invalid_ticket_id`` code,
    and the split is deliberate. A pattern violation defers to
    :meth:`PathTraversal.from_pattern_violation`, which this module declares the single
    owner of that message — so an id rejected here and the same id rejected at the HTTP
    boundary (:mod:`~factory_console.api.error_handlers`) or by
    :mod:`~factory_console.file_adapter.ticket_md` produce a word-identical envelope,
    which is the whole point of that classmethod and was lost while this function
    restated the generic reason for a violation it had already identified precisely.
    Bare ``.``/``..`` keep the generic reason because they SATISFY the pattern —
    telling an operator their id "must match ``^[A-Za-z0-9_.-]+$``" when it does would
    send them to fix an id that is already well-formed.
    """
    if re.fullmatch(TICKET_ID_PATTERN, ticket_id) is None:
        raise PathTraversal.from_pattern_violation(ticket_id)
    if ticket_id in (".", ".."):
        raise PathTraversal(ticket_id)
