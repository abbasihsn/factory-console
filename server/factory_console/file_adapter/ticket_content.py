"""Decide which FORMAT a ticket's content file is, and read it.

The console reads two ticket content formats, and this module is the one place that
chooses between them:

- **App Factory v3** — a JSON document of five structured fields, rendered to Markdown
  for display (:mod:`~factory_console.file_adapter.ticket_json`).
- **v2 and hand-written** — Markdown with optional YAML front-matter
  (:mod:`~factory_console.file_adapter.ticket_md`).

It is the FORMAT sibling of
:func:`~factory_console.file_adapter.path_safety.resolve_ticket_path`, which is the one
place that decides a ticket's LOCATION, and the two are separate on purpose: a path-safety
module must not grow opinions about file contents, and a format reader must not grow its
own containment rule. Every caller goes through both, in that order.

**Dispatch is on the manifest-declared suffix, through an explicit table.** Not on the
bytes, and not by trying one reader and falling back to the other. Sniffing would make the
answer depend on whether a Markdown ticket happens to start with ``{``; falling back would
turn a malformed v3 ticket into a Markdown document whose body is raw JSON — which is
precisely the silent degradation :class:`~factory_console.file_adapter.ticket_json.TicketInvalid`
exists to replace. The manifest declared the path, the path names the format, and a suffix
this module has no reader for is refused rather than guessed at.

**Both formats stay readable for now.** v3's own loader accepts either for one release
(App Factory plan §9) while ``factory-ticket migrate`` converts a repository, and a console
that refused ``.md`` before its own tree was migrated could not display the very project it
ships in. The Markdown half retires once that migration has happened everywhere it needs
to; :data:`_READERS` is the whole of what has to change.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from factory_console.domain.project import Project
from factory_console.domain.ticket import Ticket
from factory_console.errors import FactoryConsoleError
from factory_console.file_adapter.path_safety import resolve_ticket_path
from factory_console.file_adapter.ticket_json import read_ticket_json, render_ticket_markdown
from factory_console.file_adapter.ticket_md import read_ticket_md_text


class TicketBody(NamedTuple):
    """What a ticket's content file said, normalized across the two formats.

    ``markdown`` is what the detail view renders and what full-text search indexes — the
    file's own text for a ``.md`` ticket, and the rendered five sections for a v3 JSON
    one. Downstream consumers see Markdown either way and never learn which format the
    file was, which is the point of this module.

    ``front_matter`` is the parsed YAML mapping of a ``.md`` ticket and ``{}`` for a v3
    one. Empty rather than ``None`` because the console has always published
    ``raw['frontMatter']`` as an object, and a v3 ticket genuinely carries no front-matter
    — that is an empty answer, not a missing one.

    ``critical_files`` is ``None`` for a format that does not carry the field, and a
    non-empty list for one that does. The distinction is load-bearing: ``None`` means
    "this file says nothing about which files the ticket touches", so the manifest entry's
    own ``files`` still answers, while a list means the content file answered and its
    answer wins. Collapsing ``None`` into ``[]`` would let a v2 ticket's declared files
    be erased by a format that never had the field.
    """

    markdown: str
    front_matter: dict[str, Any]
    critical_files: list[str] | None


class TicketFormatUnsupported(FactoryConsoleError):
    """A manifest points a ticket at a file extension this console cannot read.

    Mapped to 500 for the same reason
    :class:`~factory_console.file_adapter.ticket_json.TicketInvalid` is: the manifest is
    repository data, so a path this console has no reader for is a server-side data
    problem and not something the requester did. ``details`` names the suffix, because
    "unsupported format" without saying which one leaves an operator comparing their
    manifest against a list they have to go and find.
    """

    def __init__(self, ticket_id: str, suffix: str) -> None:
        super().__init__(
            code="ticket_format_unsupported",
            message=(
                f"Ticket '{ticket_id}' has content file suffix '{suffix or '(none)'}', "
                f"which this console cannot read (expected one of: "
                f"{', '.join(sorted(_READERS))})"
            ),
            status=500,
            details={"ticketId": ticket_id, "suffix": suffix},
        )


def _read_json_body(resolved: Path, ticket_id: str, entry: dict[str, Any]) -> TicketBody:
    """Read a v3 JSON ticket and render it to the five sections."""
    content = read_ticket_json(resolved, ticket_id)
    return TicketBody(
        markdown=render_ticket_markdown(content, entry),
        front_matter={},
        critical_files=list(content.critical_files),
    )


def _read_md_body(resolved: Path, ticket_id: str, _entry: dict[str, Any]) -> TicketBody:
    """Read a Markdown ticket, splitting its optional YAML front-matter.

    Takes ``_entry`` it does not use so both readers share one signature and
    :data:`_READERS` can be a plain table rather than a dispatch with a special case in
    it. The v3 reader needs the index row because the heading it renders lives there; the
    Markdown file carries its own heading already.
    """
    front_matter, body = read_ticket_md_text(resolved, ticket_id)
    return TicketBody(markdown=body, front_matter=front_matter, critical_files=None)


_READERS: dict[str, Callable[[Path, str, dict[str, Any]], TicketBody]] = {
    ".json": _read_json_body,
    ".md": _read_md_body,
}
"""Content-file suffix → the reader that opens it. The console's whole format vocabulary.

Explicit and CLOSED, unlike the run-state vocabulary next door, and the asymmetry is
deliberate: the factory may add a run-state NAME without this console, but it cannot point
a manifest at a file format that has no reader here and expect anything but a refusal. A
suffix outside this table raises :class:`TicketFormatUnsupported` rather than falling back
to Markdown — see the module docstring for why a fallback is worse than a refusal.
"""


def read_ticket_body(
    project: Project,
    ticket_id: str,
    path: Path | None = None,
    entry: dict[str, Any] | None = None,
) -> TicketBody:
    """Resolve ``ticket_id``'s content file, dispatch on its suffix, and read it.

    ``path`` is what the manifest declared; absent, the flat ``<ticketsDir>/<id>.md``
    fallback applies (see
    :func:`~factory_console.file_adapter.path_safety.resolve_ticket_path`). ``entry`` is
    the manifest INDEX row, which the v3 reader needs for the heading it renders and the
    Markdown reader ignores; ``{}`` is a usable default that simply renders the
    placeholders.

    Raises :class:`~factory_console.file_adapter.path_safety.PathTraversal` for an unsafe
    id or an escaping declared path, :class:`TicketFormatUnsupported` for a suffix with no
    reader, and — from the readers — ``TicketFileMissing``, ``TicketFileUnreadable`` or
    :class:`~factory_console.file_adapter.ticket_json.TicketInvalid`. Every one is a mapped
    envelope; nothing escapes as an unmapped 500.
    """
    resolved = resolve_ticket_path(project.rootPath, project.ticketsDir, ticket_id, path)
    reader = _READERS.get(resolved.suffix.lower())
    if reader is None:
        raise TicketFormatUnsupported(ticket_id, resolved.suffix)
    return reader(resolved, ticket_id, entry or {})


def enrich_ticket(project: Project, stub: Ticket) -> Ticket:
    """Join a manifest ``stub`` with its on-disk body, returning a new ticket.

    Reads ``stub.id``'s content file in whichever format the manifest points at, sets
    ``bodyMarkdown`` and the resolved ``filePath``, and namespaces the parsed front-matter
    under ``raw['frontMatter']`` so top-level manifest fields keep winning by construction.
    ``bodyHtml`` is left untouched (rendered downstream). The returned
    :class:`~factory_console.domain.ticket.Ticket` is a distinct frozen instance produced
    via ``model_copy``; the stub's id already validated, so no re-validation runs.

    **``files`` may now come from the content file.** A v3 ticket declares
    ``critical_files``, and that field is not decoration — it feeds the factory's overlap
    filter, which serializes two lanes that would otherwise edit the same path off bases
    lacking each other's changes. The v3 manifest INDEX carries no ``files`` key at all, so
    without this the console would show an empty file list for every ticket in a v3
    project while the real list sat one file away, unread. A format that does not carry the
    field (``critical_files is None``) leaves the stub's manifest-derived value alone.

    ``filePath`` is the stub's, CONTAINED — not a re-derivation. Recomputing it here is
    what once made an enriched ticket disagree with the stub it came from about where its
    own file is.
    """
    body = read_ticket_body(project, stub.id, stub.filePath, stub.raw)
    update: dict[str, Any] = {
        "bodyMarkdown": body.markdown,
        "filePath": resolve_ticket_path(
            project.rootPath, project.ticketsDir, stub.id, stub.filePath
        ),
        "raw": {**stub.raw, "frontMatter": body.front_matter},
    }
    if body.critical_files is not None:
        update["files"] = body.critical_files
    return stub.model_copy(update=update)
