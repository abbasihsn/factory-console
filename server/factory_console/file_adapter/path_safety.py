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
copies were not equivalent: the one here refuses to raise on a resolution that
itself fails, which the older inline ``.resolve(strict=False)`` calls did not —
see :func:`resolve_or_none`.

**That convergence has now happened**, and this module carries its result.
:func:`contain` is the single containment implementation, and
:func:`resolve_ticket_path` above it is the single answer to "which file IS this
ticket?" — shared by the read path and the write path, and by both content
formats. It is here rather than beside either reader because the read and the
write must resolve identically or an edit merges nothing and writes an orphan file
beside the real ticket while reporting success. That happened, and it happened
because there were two derivations.

Note what this module deliberately does NOT decide: a ticket's FORMAT. It answers
where a file is and whether reading it is safe; which reader opens it belongs to
:mod:`~factory_console.file_adapter.ticket_content`. A path-safety module that
grew opinions about file contents would be answering two questions with one rule.
"""

from __future__ import annotations

import errno
import re
import stat
from pathlib import Path

from factory_console.domain.ticket import TICKET_ID_PATTERN
from factory_console.errors import FactoryConsoleError

_DEFAULT_REASON = "ticket id failed path-safety validation"
_PATTERN_VIOLATION_REASON = f"Ticket id must match {TICKET_ID_PATTERN}"

# The errno set that means "this node definitively is not there". DELIBERATELY NARROWER
# than CPython's ``pathlib._ignore_error``, which also swallows ``ELOOP``: a symlink loop
# — or a chain past ``MAXSYMLINKS`` — means the entry EXISTS and could not be RESOLVED,
# which is "I could not look", not "there is nothing to find". Nothing is lost by
# excluding it, because a DANGLING symlink already answers ``ENOENT`` on its own.
# Swallowing ``ELOOP`` reopened T80 amendment 3's fail-open through the errno table
# rather than through the walk: a looping ``merged/<id>`` answered ``False`` instead of
# raising, so ``run_state._marker_state`` stepped over it and returned a stale ``todo``
# marker — the MUTABLE state — for a ticket the factory had merged; and a looping
# run-state directory answered ``False`` from ``run_state._is_directory``, which
# ``run_state_resolver`` reads as "not a directory" and turns into the mutable
# ``unknown`` for EVERY ticket in the project. ``EBADF`` stays: a path-based ``stat()``
# cannot raise it, so it is inert either way, and dropping it would only invite someone
# to re-add ``ELOOP`` alongside it. Everything else (``EACCES`` above all) means "it may
# well be there and I could not look", which is the distinction T80's second amendment
# turns on.
#
# It lives HERE, with :func:`resolve_or_none` and :func:`within_root`, because it is the
# same KIND of rule they are: one every reader of the factory's tree must answer
# identically. It was previously defined byte-for-byte twice, in ``run_state.py`` and
# ``ledger.py``, under a comment promising to keep the copies in step by hand — and this
# module's own docstring names that arrangement as the hazard, since a future tightening
# applied to one copy and not the other leaves the write gate and the spend endpoint
# disagreeing about whether an unsearchable ``.factory/`` is absent or unreadable. One
# of those answers is ``$0.00`` on a real bill.
ABSENT_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR, errno.EBADF})


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


def is_regular_file(path: Path) -> bool:
    """``True`` if ``path`` is a regular file, ``False`` if it definitively is not, else RAISE.

    The errno-split ``stat`` + ``S_ISREG`` probe T80 made normative, hoisted here so every
    reader that needs to answer "is this node a regular file?" shares ONE implementation
    rather than restating it. :meth:`Path.is_file` cannot carry the split portably — through
    CPython 3.12 it re-raises ``EACCES``, and from 3.13 (gh-113978) it swallows every
    ``OSError`` and answers ``False`` — and this project's ``requires-python = ">=3.11"`` has
    no upper bound, so both behaviours are in the supported range. Left to the interpreter, an
    unsearchable node would report "not a regular file" on a new Python and crash the caller
    on an old one, from the same code. This module's :data:`ABSENT_ERRNOS` is the ONE errno
    set every caller of this function agrees means "definitively not there"; anything else
    propagates as "I could not look", which a caller must never collapse into ``False``.

    ``ValueError`` answers ``False``, for parity with :meth:`Path.is_file`, which treats a
    non-encodable path as absent rather than as an error.

    This was previously two byte-identical copies, in ``ledger.py`` (``find_ledger_path``) and
    ``run_state.py`` (``_is_regular_file``), kept in step only by a comment asking future
    editors to — the same drift hazard :data:`ABSENT_ERRNOS` above was hoisted here to close,
    one level up: sharing the errno *constant* alone still left the surrounding probe logic
    itself free to drift between the two readers.
    """
    try:
        return stat.S_ISREG(path.stat().st_mode)
    except OSError as exc:
        if exc.errno in ABSENT_ERRNOS:
            return False
        raise
    except ValueError:
        return False


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
    they are rejected explicitly, per the ARCHITECTURE run-state source
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


_ESCAPES_ROOT_REASON = "Ticket id resolves outside the project root"


def contain(project_root: Path, ticket_id: str, candidate: Path) -> Path:
    """Resolve ``candidate`` under ``project_root``, or raise :class:`PathTraversal`.

    A RELATIVE candidate is resolved against the PROJECT ROOT, never the process
    working directory: a manifest ``path`` is root-relative by definition, so the cwd
    was never the right base and using it would make the answer depend on where the
    server happened to be started from.

    ``candidate`` is not always derived from ``ticket_id``. A manifest-declared ``path``
    is repository data, not user input, but it is still data — a ``path`` of
    ``../../../etc/passwd`` must be refused exactly as a traversing id is, and refusing
    it less firmly merely because it arrived by a different route is how the guard
    develops a hole.

    THIS IS THE CONVERGENCE THIS MODULE'S DOCSTRING ASKED FOR. The copy it replaces
    (``ticket_md._contained``) called ``Path.resolve(strict=False)`` inline, which is
    not total: through CPython 3.12 a symlink loop escapes it as a ``RuntimeError``, an
    unmapped 500 from a module documented to raise nothing but :class:`PathTraversal`,
    while 3.13 answers the same bytes on disk cleanly. Going through
    :func:`resolve_or_none` / :func:`within_root` makes both interpreters refuse, and
    refuse for the honest reason. Note the direction of the change: a resolution that
    could not be PERFORMED now refuses rather than crashing, and it may never be read as
    containment PROVEN — ``within_root`` answers ``None`` for an unresolvable root, and
    ``None`` is a refusal here, not a pass.
    """
    absolute = candidate if candidate.is_absolute() else project_root / candidate
    resolved = resolve_or_none(absolute)
    if resolved is None or not within_root(resolved, project_root):
        raise PathTraversal(ticket_id, reason=_ESCAPES_ROOT_REASON)
    return resolved


def resolve_ticket_path(
    project_root: Path, tickets_dir: Path, ticket_id: str, path: Path | None = None
) -> Path:
    """Resolve where ``ticket_id``'s content file lives, honouring a manifest ``path``.

    The ONE place either side of the console decides which file a ticket IS. ``path`` is
    what the manifest entry declared (root-relative or absolute); absent, the flat
    ``<tickets_dir>/<id>.md`` remains the fallback for a manifest that declares none —
    a hand-written manifest, and the shape most fixtures use. The id is validated and the
    result contained in both cases, so honouring the manifest widens WHERE a ticket may
    live, never whether it may escape the root.

    It answers a LOCATION, never a FORMAT: ``.md`` and App Factory v3's ``.json`` content
    files both arrive here and leave with the same containment guarantee. Which reader
    opens the result is :mod:`~factory_console.file_adapter.ticket_content`'s decision,
    and keeping the two apart is what stops a format question from being answered by a
    path-safety module or the reverse.

    Lives here rather than beside either reader for the reason the whole module exists:
    the READ path and the WRITE path must resolve identically or an edit merges nothing
    and writes an orphan file beside the real ticket, reporting ``applied=true``. That
    happened, and it happened because there were two derivations.
    """
    validate_ticket_id_as_segment(ticket_id)
    candidate = path if path is not None else tickets_dir / f"{ticket_id}.md"
    return contain(project_root, ticket_id, candidate)
