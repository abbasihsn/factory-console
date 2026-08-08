"""Read an App Factory **v3** ticket content file: JSON in, the five sections out.

v3 stopped storing ticket prose as Markdown. A ticket is now a planner-owned JSON
document of five structured fields, and Markdown is a RENDERED VIEW of it, generated
per use and never committed (App Factory v3 plan §9). The manifest entry points at it
by ``path``:

.. code-block:: json

    {"id": "T09", "context": "…", "approach": "…",
     "critical_files": ["src/auth/routes.py"],
     "interface_data": "…",
     "verification": {"commands": ["pytest tests/auth -q"], "notes": "needs DATABASE_URL"}}

The console renders that to the same five ``## `` sections the ``.md`` files carried, so
every downstream consumer — the detail view's ``bodyHtml``, the full-text search index —
keeps working on Markdown and does not learn that the storage format changed.

**Two renderers, one output.** The factory's ``factory-ticket render`` (``lib/ticket.sh``)
emits this document for a lane to read. If the console's rendering differed, the two would
be two answers to "what is this ticket", and the one a human reviews would not be the one a
lane builds from. :func:`render_ticket_markdown` mirrors that implementation section for
section; ``tests/unit/test_ticket_json.py`` pins the shape, and the cross-repo contract
test compares against the real binary wherever App Factory is on disk.

**Validation is a Pydantic mirror of** ``schemas/ticket.schema.json``, not a JSON-Schema
run. Every model in this repo is already Pydantic v2 and every constraint the schema
states has a direct Pydantic expression (``required`` → no default, ``minLength: 1`` →
:class:`~pydantic.StringConstraints`, ``minItems: 1`` → ``min_length``,
``additionalProperties: false`` → ``extra="forbid"``). Adding ``jsonschema`` to the
runtime dependencies of a wheel published to PyPI — for one file read — buys a second
validation vocabulary and a supply-chain edge, and gives up the model that the rest of the
codebase reads and writes. The cost is that the two definitions can drift; that is what
``test_schema_mirror`` is for, and it compares against the real schema file rather than a
copy of it.

**A malformed ticket fails LOUDLY.** :class:`TicketInvalid` is a mapped 500, not an empty
body. The factory takes the same position on the migration that produced these files —
``factory-ticket migrate`` reports what it could not parse and writes nothing rather than
guessing — and for the same reason: ``critical_files`` feeds the overlap filter that
serializes two lanes editing one path, so a field silently read as empty weakens a
concurrency guard invisibly. A reader that answered ``""`` here would turn a data error
into a blank page.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from factory_console.errors import FactoryConsoleError

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
"""``{"type": "string", "minLength": 1}`` — the schema's every required scalar."""


class TicketInvalid(FactoryConsoleError):
    """A ticket content file was read but does not satisfy the v3 ticket schema.

    Distinct from its two siblings in
    :mod:`~factory_console.file_adapter.ticket_md`, and the distinction is the point:
    ``TicketFileMissing`` is "no file", ``TicketFileUnreadable`` is "bytes that are not
    text", and this is "text that IS a document and is not a ticket" — malformed JSON, a
    missing required field, an empty ``critical_files``, an unknown key the schema
    forbids.

    Mapped to 500 rather than 400 because nothing the requester did caused it: the ticket
    is repository data, and a data problem the server found is the same class as
    :class:`~factory_console.file_adapter.manifest.MalformedManifest`. ``details`` carries
    the ``ticketId`` and a ``reason`` naming which field failed — never the probed
    filesystem path, matching every other reader here — because "this ticket is broken"
    without saying where sends a human to read the whole file.
    """

    def __init__(self, ticket_id: str, reason: str) -> None:
        super().__init__(
            code="ticket_invalid",
            message=f"Ticket '{ticket_id}' does not validate against the ticket schema: {reason}",
            status=500,
            details={"ticketId": ticket_id, "reason": reason},
        )


class TicketVerification(BaseModel):
    """The ``verification`` object — how this slice is checked.

    Structured rather than prose because the factory's acceptance agent RUNS these; it
    used to re-derive commands from a paragraph the planner had already written as data.
    ``commands`` carries ``min_length=1`` from the schema's ``minItems: 1``, and the
    schema says why: under INV-42 a verification that cannot run is not a pass, so a
    ticket declaring no command can never be verified, only assumed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    commands: list[NonEmptyStr] = Field(min_length=1)
    notes: str | None = None


class TicketContent(BaseModel):
    """One v3 ticket content file — the Pydantic mirror of ``ticket.schema.json``.

    Field names are the schema's, in ``snake_case``, verbatim: they are the return
    contract of the factory's domain planner, and this model exists to accept that
    contract rather than to restate it in the console's own camelCase. The translation to
    the wire happens where every other translation does — in
    :class:`~factory_console.domain.ticket.Ticket`, built by
    :mod:`~factory_console.file_adapter.ticket_content`.

    ``extra="forbid"`` mirrors the schema's ``additionalProperties: false``, and unlike
    everywhere else in this repo it is NOT a forward-compatibility hazard to be softened.
    The factory refuses these files too, so a key the console tolerated would be a key
    the factory rejects — the console would render a ticket no lane can read, which is a
    worse answer than a loud refusal. The forward-compatible surface is the manifest
    INDEX entry (:attr:`~factory_console.domain.ticket.Ticket.raw`), which is where
    unknown fields legitimately survive.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: NonEmptyStr
    context: NonEmptyStr
    approach: NonEmptyStr
    critical_files: list[NonEmptyStr] = Field(min_length=1)
    interface_data: NonEmptyStr
    verification: TicketVerification


def _reason(exc: ValidationError) -> str:
    """One line naming which fields failed, for :class:`TicketInvalid`'s ``reason``.

    Pydantic's own ``str(exc)`` is a multi-line report carrying a documentation URL and
    the offending input; this is an error MESSAGE that reaches an HTTP envelope and a log
    line, so it is flattened to ``field: message`` pairs. The input value is deliberately
    dropped — a ticket body can be thousands of characters, and echoing it into an error
    payload is how a 500 becomes a disclosure.
    """
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors()
    )


def parse_ticket_content(ticket_id: str, text: str) -> TicketContent:
    """Parse and validate ``text`` as a v3 ticket, or raise :class:`TicketInvalid`.

    Split from the file read so the same validation is reachable from a test, from the
    write path's round-trip check, and from a caller that already holds the bytes —
    without any of them re-deciding what "valid" means.

    The ``id`` INSIDE the file is checked against the id it was reached BY. The content
    file is reached THROUGH the index, so the two disagreeing means one of them was
    hand-edited, and rendering the file anyway would put one ticket's prose under another
    ticket's heading. The schema states the same rule; it cannot enforce it, because the
    schema sees one file and never the index that pointed at it.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TicketInvalid(ticket_id, f"not valid JSON ({exc.msg} at line {exc.lineno})") from exc
    if not isinstance(payload, dict):
        raise TicketInvalid(ticket_id, f"top level is {type(payload).__name__}, expected an object")
    try:
        content = TicketContent.model_validate(payload)
    except ValidationError as exc:
        raise TicketInvalid(ticket_id, _reason(exc)) from exc
    if content.id != ticket_id:
        raise TicketInvalid(
            ticket_id,
            f"the file declares id '{content.id}', but the manifest reached it as "
            f"'{ticket_id}' — one of the two was hand-edited",
        )
    return content


def render_ticket_markdown(content: TicketContent, entry: dict[str, Any]) -> str:
    """Render ``content`` as the five ``## `` sections, under an index-derived heading.

    Mirrors ``fac_ticket_render`` (App Factory ``lib/ticket.sh``) section for section and
    in its order — Context, Staged approach, Critical files, Interface & data,
    Verification. The heading and the metadata line come from ``entry``, the manifest
    INDEX row, because that is where v3 keeps them: the content file carries no title,
    track, milestone, dependency or ``provides``, and inventing any of them here would be
    a claim the file does not make.

    The ``—``/``none`` placeholders are the factory's, not this module's preference. Two
    renderers that agree on everything but the empty cases still disagree, and the diff a
    human reads is exactly where an empty case shows up.
    """
    depends_on = [str(dep) for dep in entry.get("depends_on") or entry.get("dependsOn") or []]
    lines = [
        f"# [{content.id}] {entry.get('title') or '(untitled)'}",
        f"milestone: {entry.get('milestone') or '?'}"
        f" · track: {entry.get('track') or '?'}"
        f" · depends_on: {', '.join(depends_on) if depends_on else 'none'}"
        f" · provides: {entry.get('provides') or '—'}",
        "",
        "## Context",
        "",
        content.context,
        "",
        "## Staged approach",
        "",
        content.approach,
        "",
        "## Critical files",
        "",
        *(f"- `{path}`" for path in content.critical_files),
        "",
        "## Interface & data",
        "",
        content.interface_data,
        "",
        "## Verification",
        "",
        *(f"- `{command}`" for command in content.verification.commands),
    ]
    if content.verification.notes:
        lines += ["", f"Notes: {content.verification.notes}"]
    return "\n".join(lines)


def read_ticket_json(resolved: Path, ticket_id: str) -> TicketContent:
    """Read and validate the v3 ticket content file already resolved at ``resolved``.

    Takes a RESOLVED, CONTAINED path rather than a project and an id: containment is
    :func:`~factory_console.file_adapter.path_safety.resolve_ticket_path`'s single
    responsibility, and a reader that re-derived the path would be the second derivation
    this codebase has already paid for once.

    Read failures reuse :mod:`~factory_console.file_adapter.ticket_md`'s two error
    classes rather than minting JSON-flavoured twins. "No file" and "bytes that are not
    UTF-8 text" are facts about a FILE, identical whichever format was expected of it, and
    a caller catching ``TicketFileMissing`` must not have to catch two of them.
    """
    # Imported inside the function, not at module scope: ``ticket_content`` imports both
    # this module and ``ticket_md`` to dispatch between them, and a top-level import here
    # would make the pair mutually importable only in one order. The errors are shared
    # BECAUSE the failure is shared (see the docstring); moving them into a third module
    # to flatten this would scatter one contract across three files to save one line.
    from factory_console.file_adapter.ticket_md import TicketFileMissing, TicketFileUnreadable

    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise TicketFileMissing(ticket_id) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise TicketFileUnreadable(ticket_id) from exc
    return parse_ticket_content(ticket_id, text)
