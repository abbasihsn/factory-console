"""Parse a factory ``tickets.json`` manifest into forward-compatible ``Ticket`` stubs.

``tickets.json`` is written by the App Factory and is treated as a *tolerant*
contract: unknown entry fields are preserved verbatim on :attr:`Ticket.raw`,
missing optionals default sensibly, and ``schemaVersion`` is surfaced as a string
but never enforced — so a newer factory schema still parses here. Only genuinely
unreadable input (invalid JSON, or a top level that is not an object carrying a
``tickets`` list) raises :class:`MalformedManifest`, which the CLI maps to exit
code 3 and the backend to HTTP 500.

The stubs this module builds carry empty ``bodyMarkdown`` / ``bodyHtml``; the
rendered ticket ``.md`` body is joined in later (T13). The ``open`` here is one of
the file-adapter's sanctioned I/O sites.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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


def load_manifest(manifest_path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    """Read and parse ``tickets.json``, returning ``(schemaVersion, tickets)``.

    ``schemaVersion`` is coerced to ``str`` when present (the fixtures carry it as
    the int ``1`` -> ``"1"``) and ``None`` when absent — surfaced, never enforced.
    ``tickets`` is the raw list of entry dicts, each later turned into a
    :class:`Ticket` stub by :func:`manifest_entry_to_ticket_stub`.

    Raises:
        MalformedManifest: if the file cannot be read as UTF-8 text (a non-UTF-8
            manifest, a permission-denied read, or a vanished file), if it is not
            valid JSON, if the top level is not a JSON object, if ``tickets`` is
            missing or not a list, or if any ``tickets`` entry is not a JSON object.
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
    for index, entry in enumerate(tickets):
        if not isinstance(entry, dict):
            cause = ValueError(
                f"'tickets'[{index}] must be a JSON object, got {type(entry).__name__}"
            )
            raise MalformedManifest(manifest_path, cause=cause) from cause
    schema_version = parsed.get("schemaVersion")
    schema_version_str = None if schema_version is None else str(schema_version)
    return schema_version_str, tickets


def manifest_entry_to_ticket_stub(entry: dict[str, Any], tickets_dir: Path) -> Ticket:
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
        dependsOn=entry.get("dependsOn", []),
        provides=_provides_to_list(entry.get("provides")),
        files=entry.get("files", []),
        filePath=tickets_dir / f"{entry_id}.md",
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
    entry a dict) but not that an entry carries a valid ``id``; a per-entry
    ``KeyError`` (missing ``id``) or pydantic ``ValidationError`` (an ``id`` failing
    ``TICKET_ID_PATTERN``, or another mistyped field) is therefore re-raised here as
    :class:`MalformedManifest` so a hand-edited manifest fails with the documented
    envelope (CLI exit 3 / HTTP 500) rather than a raw traceback or a bare 500.
    """
    manifest_path = project.ticketsManifestPath
    _schema_version, entries = load_manifest(manifest_path)
    for entry in entries:
        try:
            yield manifest_entry_to_ticket_stub(entry, project.ticketsDir)
        except (KeyError, ValidationError) as exc:
            raise MalformedManifest(manifest_path, cause=exc) from exc
