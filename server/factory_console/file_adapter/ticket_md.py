"""Read one Markdown ticket: split YAML front-matter from body markdown.

The v2 and hand-written ticket format. App Factory v3 stores ticket content as JSON
(:mod:`~factory_console.file_adapter.ticket_json`); which of the two a given ticket is
belongs to :mod:`~factory_console.file_adapter.ticket_content`, and nothing here decides
it. This module is only the Markdown half.

It no longer resolves paths. Where a ticket's file LIVES is
:func:`~factory_console.file_adapter.path_safety.resolve_ticket_path`'s single
responsibility — one derivation shared by the read path, the write path and both formats,
because two derivations is what once made an edit merge nothing, write an orphan
``<id>.md`` beside the real ticket, and report ``applied=true``.

The two error classes below stay here despite serving both formats, and that is not an
oversight: "no file" and "bytes that are not UTF-8 text" are facts about a FILE, identical
whichever format was expected of it, so a caller catching :class:`TicketFileMissing` must
not have to catch two of them. :class:`~factory_console.file_adapter.ticket_json.TicketInvalid`
is the one that is genuinely format-specific — it means the text IS a document and is not a
ticket — and it lives with the format that can raise it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from factory_console.errors import FactoryConsoleError

_FENCE = "---"
"""A front-matter fence line — exactly three dashes on their own line."""


class TicketFileMissing(FactoryConsoleError):
    """A ticket id is well-formed and contained, but no content file exists.

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
    """A ticket file exists but cannot be read as UTF-8 text.

    Distinct from :class:`TicketFileMissing` (no file at all): the path resolves to
    something present that still cannot be turned into text — a directory at the ticket's
    path (:class:`IsADirectoryError`), a permission-denied read
    (:class:`PermissionError`), or bytes that are not valid UTF-8
    (:class:`UnicodeDecodeError`). Mapped to HTTP 500 (a server-side data problem, like
    :class:`~factory_console.file_adapter.manifest.MalformedManifest`) so the edge layer
    renders a graceful envelope instead of leaking a raw traceback. ``details`` carries
    the ``ticketId`` only — never the probed filesystem path.
    """

    def __init__(self, ticket_id: str) -> None:
        super().__init__(
            code="ticket_file_unreadable",
            message=f"Ticket file for id '{ticket_id}' could not be read as UTF-8 text",
            status=500,
            details={"ticketId": ticket_id},
        )


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


def read_ticket_md_text(resolved: Path, ticket_id: str) -> tuple[dict[str, Any], str]:
    """Read the Markdown ticket at ``resolved`` and return ``(front_matter, body)``.

    Front-matter is the parsed leading YAML mapping; ``body`` is the text after the fence.
    When there is no fence, when the YAML is malformed, or when it parses to a
    non-mapping, the front-matter is ``{}`` and the body is the full original text (fences
    included) — this function never raises on malformed YAML. That tolerance is the
    Markdown format's own and does not extend to
    :mod:`~factory_console.file_adapter.ticket_json`, which refuses a document it cannot
    validate: front-matter is optional metadata a hand-written ticket may reasonably
    fumble, while a v3 ticket's fields are the ticket.

    Takes a RESOLVED, CONTAINED path rather than a project and an id — containment is
    :func:`~factory_console.file_adapter.path_safety.resolve_ticket_path`'s job, and a
    reader that re-derived the path would be the second derivation this codebase has
    already paid for once. Raises :class:`TicketFileMissing` when the file does not exist
    and :class:`TicketFileUnreadable` when it exists but is not UTF-8 text, so every read
    failure surfaces as a mapped envelope rather than escaping as an unmapped 500.
    """
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
