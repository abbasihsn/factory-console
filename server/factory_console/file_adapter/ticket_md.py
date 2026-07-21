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
:class:`TicketFileMissing` (status 404, ``ticket_file_missing``).
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


def _safe_resolve(project: Project, ticket_id: str) -> Path:
    """Resolve ``<ticketsDir>/<ticket_id>.md``, refusing any unsafe id.

    Raises :class:`PathTraversal` when the id fails :data:`TICKET_ID_PATTERN` or
    when the resolved path is not contained under ``project.rootPath``. Both
    sides of the containment check are resolved so symlinked temp roots (e.g.
    ``/tmp`` and ``/var/folders`` on macOS) don't cause a false negative.
    """
    if _TICKET_ID_RE.fullmatch(ticket_id) is None:
        raise PathTraversal.from_pattern_violation(ticket_id)
    candidate = (project.ticketsDir / f"{ticket_id}.md").resolve(strict=False)
    if not candidate.is_relative_to(project.rootPath.resolve()):
        raise PathTraversal(ticket_id, reason=_ID_ESCAPES_ROOT)
    return candidate


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


def read_ticket_md(project: Project, ticket_id: str) -> tuple[dict[str, Any], str]:
    """Read a ticket ``.md`` and return ``(front_matter, body_markdown)``.

    Front-matter is the parsed leading YAML mapping; ``body_markdown`` is the
    text after the fence. When there is no fence, when the YAML is malformed, or
    when it parses to a non-mapping, the front-matter is ``{}`` and the body is
    the full original text (fences included) — this function never raises on
    malformed YAML.

    Raises :class:`PathTraversal` for an unsafe id and :class:`TicketFileMissing`
    when the resolved file does not exist.
    """
    path = _safe_resolve(project, ticket_id)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise TicketFileMissing(ticket_id) from exc

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
    front_matter, body = read_ticket_md(project, stub.id)
    new_raw = {**stub.raw, "frontMatter": front_matter}
    return stub.model_copy(
        update={
            "bodyMarkdown": body,
            "filePath": _safe_resolve(project, stub.id),
            "raw": new_raw,
        }
    )
