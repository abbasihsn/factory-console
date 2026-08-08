"""Parse a factory ``tickets.json`` manifest into forward-compatible ``Ticket`` stubs.

``tickets.json`` is written by the App Factory and is treated as a *tolerant*
contract: unknown entry fields are preserved verbatim on :attr:`Ticket.raw`,
missing optionals default sensibly, and ``schemaVersion`` is surfaced as a string
but never enforced — so a newer factory schema still parses here. Only genuinely
unreadable input (invalid JSON, a top level that is not an object carrying a
``tickets`` list, or two entries sharing one ticket id) raises
:class:`MalformedManifest`, which the CLI maps to exit code 3 and the backend to
HTTP 500.

The stubs this module builds carry empty ``bodyMarkdown`` / ``bodyHtml``; the
rendered ticket ``.md`` body is joined in later (T13). The ``open`` here is one of
the file-adapter's sanctioned I/O sites.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

from pydantic import ValidationError

from factory_console.domain import Project, Ticket
from factory_console.errors import FactoryConsoleError


class MalformedManifest(FactoryConsoleError):
    """Raised when a ``tickets.json`` manifest cannot be parsed into a ticket list.

    Carries ``code='malformed_manifest'`` / ``status=500`` (the CLI maps this to
    exit code 3). ``details`` holds only the manifest path — never file contents —
    so the error envelope cannot leak manifest data. The original parse failure,
    when there is one, is kept on :attr:`cause` and chained via ``raise ... from``
    at the raise site.
    """

    def __init__(self, path: Path, cause: Exception | None = None) -> None:
        message = (
            f"Malformed manifest at {path}: {cause}"
            if cause is not None
            else f"Malformed manifest at {path}"
        )
        super().__init__(
            code="malformed_manifest",
            message=message,
            status=500,
            details={"path": str(path)},
        )
        self.path = path
        self.cause = cause


def _provides_to_list(value: object) -> list[str]:
    """Coerce a manifest ``provides`` value into ``Ticket.provides``' ``list[str]``.

    WHY: the manifest schema stores ``provides`` as a single scalar string, but the
    model types it as ``list[str]``. A non-empty string wraps to one element; an
    empty/missing string (or ``None``) becomes ``[]``; an existing list passes
    through for the model to validate. The unmodified value is still preserved on
    :attr:`Ticket.raw`, so this normalization loses nothing.
    """
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return value
    return []


class ManifestDocument(NamedTuple):
    """One validated read of ``tickets.json``: its text, its object, its entries.

    The WRITE path needs all three from the SAME read. It used to call
    :func:`load_manifest` for the entries and then re-read the file for the raw
    text and full object, computing an entry INDEX against the first parse and
    applying it to the second — so a manifest rewritten in between (the App
    Factory owns this file and the console does not control it) had the stale
    index address a different entry, and an edit or delete silently hit the wrong
    ticket. ``tickets`` is ``obj["tickets"]`` — the same list object, not a copy —
    so a caller mutating an entry through either view mutates one document.
    """

    schemaVersion: str | None
    tickets: list[dict[str, Any]]
    obj: dict[str, Any]
    rawText: str


def load_manifest(manifest_path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    """Read and parse ``tickets.json``, returning ``(schemaVersion, tickets)``.

    The READ path's view of :func:`load_manifest_document`, which is where the
    parsing and validation live. Callers that also need to re-serialize the file
    (the write path) must use that instead, so the entries they index and the
    object they mutate come from one read.
    """
    document = load_manifest_document(manifest_path)
    return document.schemaVersion, document.tickets


def load_manifest_document(manifest_path: Path) -> ManifestDocument:
    """Read, parse and validate ``tickets.json`` ONCE.

    ``schemaVersion`` is coerced to ``str`` when present (the fixtures carry it as
    the int ``1`` -> ``"1"``) and ``None`` when absent — surfaced, never enforced.
    ``tickets`` is the raw list of entry dicts, each later turned into a
    :class:`Ticket` stub by :func:`manifest_entry_to_ticket_stub`. ``obj`` is the
    whole parsed document, carrying the top-level keys (``project``,
    ``schemaVersion``) that a re-serialization must preserve, and ``rawText`` is
    the exact text those came from — the "current" side of a rendered diff.

    Raises:
        MalformedManifest: if the file cannot be read as UTF-8 text (a non-UTF-8
            manifest, a permission-denied read, or a vanished file), if it is not
            valid JSON, if the top level is not a JSON object, if ``tickets`` is
            missing or not a list, if any ``tickets`` entry is not a JSON object,
            or if two entries carry the same ticket ``id``.
    """
    # Guard the read itself, not just json.loads: a non-UTF-8 manifest raises
    # UnicodeDecodeError and a permission-denied/vanished file raises OSError, and
    # neither is a JSONDecodeError — so without this they would escape as an
    # unmapped error (CLI exit 1 instead of the documented 3, a raw 500 on the
    # request path). Mirrors read_ticket_md's read guard.
    try:
        with open(manifest_path, encoding="utf-8") as manifest_file:
            raw_text = manifest_file.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise MalformedManifest(manifest_path, cause=exc) from exc
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise MalformedManifest(manifest_path, cause=exc) from exc
    if not isinstance(parsed, dict):
        cause = ValueError(f"manifest top level must be a JSON object, got {type(parsed).__name__}")
        raise MalformedManifest(manifest_path, cause=cause) from cause
    tickets = parsed.get("tickets")
    if not isinstance(tickets, list):
        cause = ValueError(f"'tickets' must be a list, got {type(tickets).__name__}")
        raise MalformedManifest(manifest_path, cause=cause) from cause
    # Ids must be unique, and the check has to happen HERE — eagerly, before any
    # entry is yielded — not while streaming stubs: ``get_ticket`` stops at the
    # first matching id, so a later duplicate would go unnoticed there and the
    # list/detail/deps views would silently disagree (list emits two rows, detail
    # returns the first entry, deps the last). A duplicate id is a malformed
    # manifest, so it is REJECTED rather than de-duplicated. Only the entry
    # indexes are reported, never the id itself, keeping manifest content out of
    # the error envelope.
    seen_ids: dict[str, int] = {}
    for index, entry in enumerate(tickets):
        if not isinstance(entry, dict):
            cause = ValueError(
                f"'tickets'[{index}] must be a JSON object, got {type(entry).__name__}"
            )
            raise MalformedManifest(manifest_path, cause=cause) from cause
        entry_id = entry.get("id")
        # A non-str/missing id is not this check's business: it surfaces per-entry
        # as KeyError/ValidationError from manifest_entry_to_ticket_stub.
        if not isinstance(entry_id, str):
            continue
        if entry_id in seen_ids:
            cause = ValueError(
                f"duplicate ticket id at 'tickets'[{index}], "
                f"first seen at 'tickets'[{seen_ids[entry_id]}]"
            )
            raise MalformedManifest(manifest_path, cause=cause) from cause
        seen_ids[entry_id] = index
    schema_version = parsed.get("schemaVersion")
    schema_version_str = None if schema_version is None else str(schema_version)
    return ManifestDocument(
        schemaVersion=schema_version_str, tickets=tickets, obj=parsed, rawText=raw_text
    )


def _entry_depends_on(entry: dict[str, Any]) -> list[str]:
    """Read an entry's dependency ids, accepting both spellings of the key.

    THE FACTORY WRITES ``depends_on``; this reader only ever looked for
    ``dependsOn``. Every real manifest therefore parsed with an EMPTY dependency
    list and no error anywhere — the graph rendered every ticket as an unconnected
    node, and dep-neighborhood reported ``depCount: 0`` for a 101-ticket DAG.
    Measured on this repository: 101 nodes, 0 edges.

    Nothing caught it because ``tests/fixtures/projects/*`` were authored with
    ``dependsOn``, so the fixtures agreed with the reader and neither agreed with
    the producer. This is the defect v2.1 was entirely about — *"built against a
    contract that describes something the factory does not write"* — surviving in
    a second reader because only run-state was ever checked against reality.

    ``depends_on`` wins when both are present: it is what the producer writes, and
    a manifest carrying both is likelier mid-migration than deliberately split.
    """
    for key in DEPENDS_ON_KEYS:
        value = entry.get(key)
        if value:
            return list(value)
    return []


DEPENDS_ON_KEYS = ("depends_on", "dependsOn")
"""Both spellings of the dependency key, MOST AUTHORITATIVE FIRST.

Read by :func:`_entry_depends_on` and by the write path, which must agree with it
or an edit is invisible. It did not: the writer emitted ``dependsOn`` while this
reader resolves ``depends_on`` first, so editing a factory-written ticket left the
stale ``depends_on`` in place, added a second contradictory key, and handed the OLD
dependencies back on every subsequent read — a silent no-op behind a ``200 OK``,
and no way to clear a dependency at all.
"""


def ticket_file_path(entry: dict[str, Any], tickets_dir: Path, root: Path | None = None) -> Path:
    """Where this ticket's ``.md`` lives.

    THE MANIFEST ALREADY SAYS, and nothing read it. Real manifests carry a
    root-relative ``path`` (``docs/planning/tickets/v2.1/T84-spend-view.md``)
    because the factory files tickets under a milestone directory with a slug in
    the filename. This module assumed a flat ``<ticketsDir>/<id>.md`` and computed
    that instead, so every ticket-detail request 404'd — all 101 of them on this
    repository — while the correct path sat unread in the same entry.

    The flat form remains the fallback for a manifest with no ``path``, which is
    what the fixtures use and what a hand-written manifest may reasonably be.
    Containment against the project root is NOT enforced here: this returns a
    candidate, and the reader that opens it (``ticket_md``) is the one place that
    can refuse an escaping path with the right error.
    """
    declared = entry.get("path")
    if declared and root is not None:
        candidate = Path(str(declared))
        return candidate if candidate.is_absolute() else root / candidate
    return tickets_dir / f"{entry['id']}.md"


def manifest_entry_to_ticket_stub(
    entry: dict[str, Any], tickets_dir: Path, root: Path | None = None
) -> Ticket:
    """Build a bare :class:`Ticket` stub from one manifest entry.

    Maps the entry's camelCase fields onto the model with sensible defaults and
    preserves the *entire* unmodified entry on :attr:`Ticket.raw`, so unknown
    fields (e.g. ``estimate``) survive for forward-compatibility. The ticket id is
    passed straight to the model, which validates it against ``TICKET_ID_PATTERN``;
    a missing or malformed id surfaces as an error rather than being papered over.
    ``filePath`` is computed as ``tickets_dir / f'{id}.md'``; ``bodyMarkdown`` /
    ``bodyHtml`` are empty here — the rendered ``.md`` body is joined in later (T13).
    """
    entry_id = entry["id"]
    return Ticket(
        id=entry_id,
        title=entry.get("title", ""),
        status=entry.get("status", ""),
        track=entry.get("track"),
        milestone=entry.get("milestone"),
        dependsOn=_entry_depends_on(entry),
        provides=_provides_to_list(entry.get("provides")),
        files=entry.get("files", []),
        filePath=ticket_file_path(entry, tickets_dir, root),
        bodyMarkdown="",
        bodyHtml="",
        raw=entry,
    )


def iter_ticket_stubs(project: Project) -> Iterator[Ticket]:
    """Yield a :class:`Ticket` stub for every entry in the project's manifest.

    Reads ``project.ticketsManifestPath`` via :func:`load_manifest` (the surfaced
    ``schemaVersion`` is not needed here) and computes each stub's ``filePath``
    from ``project.ticketsDir``. Lazy by design so a large manifest is streamed
    rather than materialized; a :class:`MalformedManifest` from :func:`load_manifest`
    propagates to the caller on first iteration.

    ``load_manifest`` validates STRUCTURE (top-level object, ``tickets`` list, each
    entry a dict, no repeated ticket id) but not that an entry carries a valid
    ``id`` in the first place; a per-entry
    ``KeyError`` (missing ``id``) or pydantic ``ValidationError`` (an ``id`` failing
    ``TICKET_ID_PATTERN``, or another mistyped field) is therefore re-raised here as
    :class:`MalformedManifest` so a hand-edited manifest fails with the documented
    envelope (CLI exit 3 / HTTP 500) rather than a raw traceback or a bare 500.
    """
    manifest_path = project.ticketsManifestPath
    _schema_version, entries = load_manifest(manifest_path)
    for entry in entries:
        try:
            yield manifest_entry_to_ticket_stub(entry, project.ticketsDir, project.rootPath)
        except (KeyError, ValidationError) as exc:
            raise MalformedManifest(manifest_path, cause=exc) from exc
