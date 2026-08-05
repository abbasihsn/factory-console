"""Read one ticket ``.md`` by id: split YAML front-matter from body markdown.

Ticket bodies live at ``<project.ticketsDir>/<id>.md``. This module reads a
single file by ticket id and separates the optional leading ``---`` fenced YAML
front-matter from the markdown body. It enforces defense-in-depth path safety:
the id is re-validated against :data:`TICKET_ID_PATTERN` (the single source of
truth, imported verbatim) and the *resolved* path must stay under the project
root, so a symlink pointing outside the project can never be read.

Both unsafe-id causes — a pattern violation and a resolved path that escapes the
root — surface as the same transport contract (:class:`PathTraversal`, status
400, ``invalid_ticket_id``); a valid id with no file on disk surfaces as
:class:`TicketFileMissing` (status 404, ``ticket_file_missing``); and a resolved
path that exists but cannot be read as UTF-8 (a directory, a permission-denied
read, or non-UTF-8 bytes) surfaces as :class:`TicketFileUnreadable` (status 500,
``ticket_file_unreadable``) rather than escaping as an unmapped 500.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from factory_console.domain.project import Project
from factory_console.domain.ticket import TICKET_ID_PATTERN, Ticket
from factory_console.errors import FactoryConsoleError
from factory_console.file_adapter.path_safety import PathTraversal

_TICKET_ID_RE = re.compile(TICKET_ID_PATTERN)
"""The canonical ticket-id pattern compiled once at import for re-validation."""

_FENCE = "---"
"""A front-matter fence line — exactly three dashes on their own line."""

_ID_ESCAPES_ROOT = "Ticket id resolves outside the project root"


class TicketFileMissing(FactoryConsoleError):
    """A ticket id is well-formed and contained, but no ``.md`` file exists.

    ``details`` carries the ``ticketId`` only — never the probed filesystem path.
    """

    def __init__(self, ticket_id: str) -> None:
        super().__init__(
            code="ticket_file_missing",
            message=f"No ticket file found for id '{ticket_id}'",
            status=404,
            details={"ticketId": ticket_id},
        )


class TicketFileUnreadable(FactoryConsoleError):
    """A ticket ``.md`` exists but cannot be read as UTF-8 text.

    Distinct from :class:`TicketFileMissing` (no file at all): the path resolves to
    something present that still cannot be turned into text — a directory at the
    ``.md`` path (:class:`IsADirectoryError`), a permission-denied read
    (:class:`PermissionError`), or bytes that are not valid UTF-8
    (:class:`UnicodeDecodeError`). Mapped to HTTP 500 (a server-side data problem,
    like :class:`~factory_console.file_adapter.manifest.MalformedManifest`) so the
    edge layer renders a graceful envelope instead of leaking a raw traceback.
    ``details`` carries the ``ticketId`` only — never the probed filesystem path.
    """

    def __init__(self, ticket_id: str) -> None:
        super().__init__(
            code="ticket_file_unreadable",
            message=f"Ticket file for id '{ticket_id}' could not be read as UTF-8 text",
            status=500,
            details={"ticketId": ticket_id},
        )


def _safe_resolve(project: Project, ticket_id: str) -> Path:
    """Resolve ``<ticketsDir>/<ticket_id>.md``, refusing any unsafe id.

    Raises :class:`PathTraversal` when the id fails :data:`TICKET_ID_PATTERN` or
    when the resolved path is not contained under ``project.rootPath``. Both
    sides of the containment check are resolved so symlinked temp roots (e.g.
    ``/tmp`` and ``/var/folders`` on macOS) don't cause a false negative.
    """
    if _TICKET_ID_RE.fullmatch(ticket_id) is None:
        raise PathTraversal.from_pattern_violation(ticket_id)
    return _contained(project, ticket_id, project.ticketsDir / f"{ticket_id}.md")


def _contained(project: Project, ticket_id: str, candidate: Path) -> Path:
    """Resolve ``candidate`` and refuse it if it escapes the project root.

    Split out of :func:`_safe_resolve` because the candidate no longer always
    comes from the id. A manifest-declared ``path`` is repository data, not user
    input, but it is still data — a ``path`` of ``../../../etc/passwd`` must be
    refused exactly as a traversing id is, and it would be worse to trust it
    merely because it arrived by a different route.
    """
    # A RELATIVE candidate is resolved against the PROJECT ROOT, not the process
    # working directory. `Path.resolve` would use the cwd, which makes the answer
    # depend on where the server happened to be started from — and a manifest's
    # `path` is root-relative by definition, so the cwd was never the right base.
    root = project.rootPath.resolve()
    absolute = candidate if candidate.is_absolute() else root / candidate
    resolved = absolute.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise PathTraversal(ticket_id, reason=_ID_ESCAPES_ROOT)
    return resolved


def _split_front_matter(text: str) -> tuple[str | None, str]:
    """Split a leading ``---`` fenced YAML block from the markdown body.

    Returns ``(front_matter_yaml, body)``. ``front_matter_yaml`` is ``None`` when
    ``text`` has no leading fence (or an opening fence with no closing fence),
    and an empty string when the fence is present but empty — letting the caller
    distinguish "no front-matter" from "empty front-matter". ``body`` is the text
    after the closing fence, with the fences excluded.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != _FENCE:
        return None, text
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == _FENCE:
            front_matter = "".join(lines[1:index])
            body = "".join(lines[index + 1 :])
            return front_matter, body
    # Opening fence with no closing fence: not valid front-matter, keep it all.
    return None, text


def read_ticket_md(
    project: Project, ticket_id: str, path: Path | None = None
) -> tuple[dict[str, Any], str]:
    """Read a ticket ``.md`` and return ``(front_matter, body_markdown)``.

    Front-matter is the parsed leading YAML mapping; ``body_markdown`` is the
    text after the fence. When there is no fence, when the YAML is malformed, or
    when it parses to a non-mapping, the front-matter is ``{}`` and the body is
    the full original text (fences included) — this function never raises on
    malformed YAML.

    Raises :class:`PathTraversal` for an unsafe id, :class:`TicketFileMissing`
    when the resolved file does not exist, and :class:`TicketFileUnreadable` when
    it exists but cannot be read as UTF-8 (a directory, a permission-denied read,
    or non-UTF-8 bytes) — so every read failure surfaces as a mapped envelope
    rather than escaping as an unmapped 500.
    """
    # `path` is the file the MANIFEST declared for this ticket, passed by
    # enrich_ticket. Without it this function re-derived <ticketsDir>/<id>.md and
    # ignored what the manifest said, which is why every ticket-detail request
    # 404'd against a real factory repository (tickets live under a milestone
    # directory with a slug in the name). Absent, the flat form is still the
    # fallback — a hand-written manifest need not declare paths. Either way the
    # id is re-validated and the result is contained, so honouring the manifest
    # widens where a ticket may live, never whether it may escape the root.
    if path is None:
        resolved = _safe_resolve(project, ticket_id)
    else:
        if _TICKET_ID_RE.fullmatch(ticket_id) is None:
            raise PathTraversal.from_pattern_violation(ticket_id)
        resolved = _contained(project, ticket_id, path)
    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise TicketFileMissing(ticket_id) from exc
    except (OSError, UnicodeDecodeError) as exc:
        # FileNotFoundError is handled above; every OTHER read failure —
        # IsADirectoryError/PermissionError (both OSError) or a non-UTF-8
        # UnicodeDecodeError — maps to the graceful unreadable envelope.
        raise TicketFileUnreadable(ticket_id) from exc

    front_matter_yaml, body = _split_front_matter(text)
    if front_matter_yaml is None:
        return {}, text
    try:
        parsed = yaml.safe_load(front_matter_yaml)
    except yaml.YAMLError:
        return {}, text
    if parsed is None:
        return {}, body
    if isinstance(parsed, dict):
        return parsed, body
    return {}, text


def enrich_ticket(project: Project, stub: Ticket) -> Ticket:
    """Join a manifest ``stub`` with its on-disk body, returning a new ticket.

    Reads ``stub.id``'s ``.md``, sets ``bodyMarkdown`` and the resolved
    ``filePath``, and namespaces the parsed front-matter under
    ``raw['frontMatter']`` so top-level manifest fields keep winning by
    construction. ``bodyHtml`` is left untouched (rendered downstream). The
    returned :class:`Ticket` is a distinct frozen instance produced via
    ``model_copy``; the stub's id already validated, so no re-validation runs.
    """
    front_matter, body = read_ticket_md(project, stub.id, stub.filePath)
    new_raw = {**stub.raw, "frontMatter": front_matter}
    return stub.model_copy(
        update={
            "bodyMarkdown": body,
            # The stub's filePath, contained — NOT a re-derivation. Recomputing it
            # here is what made the enriched ticket disagree with the stub it came
            # from about where its own file is.
            "filePath": _contained(project, stub.id, stub.filePath),
            "raw": new_raw,
        }
    )
