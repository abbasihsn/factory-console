"""Pure write-render: compute the DESIRED text of the three coupled files.

Given a project's CURRENT on-disk state plus a validated create/edit/delete, this
module computes exactly what each of ``docs/planning/tickets.json``, the ticket's
content file, and ``ROADMAP.md`` should contain — as a set of
:class:`PlannedChange` — WITHOUT writing anything. Kept pure so the dry-run diff
engine and the atomic co-writer consume the identical planned change-set and can
never disagree about what would change.

**The console writes App Factory v3 tickets, and only those.** A created ticket is a
JSON content file whose path the manifest entry declares; an edit rewrites one. That is
narrower than what it READS (:mod:`~factory_console.file_adapter.ticket_content` still
accepts Markdown, so a project mid-migration stays viewable) and the asymmetry is the
point: the write DTOs no longer carry a field that could express a Markdown body, so an
edit landing on one is refused with ``factory-ticket migrate`` rather than converted in
place. A refusal naming the command is recoverable in one step; a lossy conversion
nobody asked for is not.

Forward-compatibility now applies to ONE of the two files. An edit MERGES onto the
existing raw manifest entry, so unknown index fields (``estimate``, a legacy ``files``)
survive verbatim — the same tolerance the read path keeps on :attr:`Ticket.raw`. The
content file is REPLACED, because its schema forbids extra keys and requires every
field: there is nothing a merge could preserve that the supplied fields do not say, and
what it might preserve is what the factory itself rejects.

Path safety is defense-in-depth: every ticket id is re-validated and resolved under
``project.rootPath`` through
:func:`~factory_console.file_adapter.path_safety.resolve_ticket_path` — the same single
resolver the read path uses — so a slash/``..`` id can never escape the tickets
directory, and an edit can never write to a file the reader would not have read.

Only the THREE known relative paths are ever emitted — the manifest, the ticket's
content file, and the roadmap — never a run-state path. Nothing here writes, makes
directories, or has any side effect; it only reads.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from factory_console.domain.project import Project
from factory_console.domain.ticket import TICKET_ID_PATTERN
from factory_console.domain.write import TicketContentFields, TicketDraft, TicketEdit
from factory_console.errors import FactoryConsoleError
from factory_console.file_adapter.manifest import DEPENDS_ON_KEYS, load_manifest_document
from factory_console.file_adapter.path_safety import PathTraversal, resolve_ticket_path
from factory_console.file_adapter.roadmap_parse import _LIST_ITEM_RE, _extract_ticket_id
from factory_console.file_adapter.ticket_content import TicketFormatRetired, TicketFormatUnsupported
from factory_console.file_adapter.ticket_json import parse_ticket_content
from factory_console.file_adapter.ticket_md import TicketFileUnreadable

_TICKET_ID_RE = re.compile(TICKET_ID_PATTERN)
"""The canonical ticket-id pattern compiled once at import for re-validation."""

_DEFAULT_STATUS = "todo"
"""The status a freshly created ticket carries in the manifest."""

_H2_PREFIX = "## "
"""A roadmap milestone heading opener (an h2), matching ``roadmap_parse``."""

# A roadmap list item's leading structure: optional indentation, the bullet
# marker, and an optional GitHub-style checkbox. Used to rewrite an item's label
# IN PLACE on edit while preserving its indentation, bullet, and done-state.
_ITEM_PREFIX_RE = re.compile(r"^(?P<indent>\s*)(?P<bullet>[-*]\s+)(?P<checkbox>\[[ xX]\]\s*)?")


class TicketAlreadyExists(FactoryConsoleError):
    """A create targets a ticket id already present in the manifest.

    ``details`` carries the ``ticketId`` only — never a resolved filesystem path.
    """

    def __init__(self, ticket_id: str) -> None:
        super().__init__(
            code="ticket_already_exists",
            message=f"A ticket with id '{ticket_id}' already exists",
            status=409,
            details={"ticketId": ticket_id},
        )


class UnknownTicket(FactoryConsoleError):
    """An edit/delete targets a ticket id absent from the manifest.

    ``details`` carries the ``ticketId`` only — never a resolved filesystem path.
    """

    def __init__(self, ticket_id: str) -> None:
        super().__init__(
            code="ticket_not_found",
            message=f"No ticket found for id '{ticket_id}'",
            status=404,
            details={"ticketId": ticket_id},
        )


class PlannedChange(BaseModel):
    """One file's desired content, computed but not yet written.

    ``currentText is None`` means the file is absent on disk (a create);
    ``newText is None`` means the file should be deleted. ``relPath`` is the path
    relative to the project root (POSIX) — one of exactly three values: the
    manifest, the ticket ``.md``, or the roadmap. Frozen so a computed change-set
    is an immutable value shared by the diff engine and the co-writer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    relPath: str
    currentText: str | None = None
    newText: str | None = None


# --------------------------------------------------------------------------- #
# Path safety & relative paths
# --------------------------------------------------------------------------- #


def _require_safe_id(ticket_id: str) -> None:
    """Raise :class:`PathTraversal` unless ``ticket_id`` matches the canonical pattern."""
    if _TICKET_ID_RE.fullmatch(ticket_id) is None:
        raise PathTraversal.from_pattern_violation(ticket_id)


def _entry_content_path(project: Project, entry: Mapping[str, Any], ticket_id: str) -> Path:
    """Resolve the content file an EXISTING manifest entry declares.

    The write path's half of the rule the read path already follows
    (:func:`~factory_console.file_adapter.manifest.ticket_file_path`): the entry
    says where its body file is, and it is the entry — not the id — that decides.
    Deriving ``<ticketsDir>/<id>.md`` here regardless is what made an edit write an
    orphan file beside the real one and a delete unlink nothing, both while
    answering ``applied=true``.
    """
    declared = entry.get("path")
    return resolve_ticket_path(
        project.rootPath, project.ticketsDir, ticket_id, Path(str(declared)) if declared else None
    )


def _rel_posix(path: Path, root: Path) -> str:
    """Return ``path`` relative to the project ``root`` as a POSIX string.

    Both sides are resolved so a symlinked root does not defeat ``relative_to``.
    """
    return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()


# --------------------------------------------------------------------------- #
# Reading current on-disk state
# --------------------------------------------------------------------------- #


def _read_text_or_none(path: Path) -> str | None:
    """Return ``path``'s UTF-8 text, or ``None`` when it is absent/unreadable.

    A missing file (``FileNotFoundError``) yields ``None`` so the caller treats it
    as create-like. Any other read failure (permission, non-UTF-8) also yields
    ``None`` rather than raising — this is best-effort *current* text for diffing,
    and swallowing here guarantees no filesystem path leaks in a raised error.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _read_content_or_none(ticket_id: str, path: Path) -> str | None:
    """Return a ticket ``.md``'s text, or ``None`` only when it is genuinely ABSENT.

    Unlike :func:`_read_text_or_none`, an existing-but-UNREADABLE file (a directory,
    a permission-denied read, non-UTF-8 bytes) raises :class:`TicketFileUnreadable`
    instead of reading as ``None``. An edit rebuilds the ``.md`` from what is on
    disk, so collapsing "unreadable" into "absent" would overwrite — and destroy —
    the very front matter the merge exists to preserve, on a file the server could
    not even read. Failing closed surfaces it as the same mapped envelope the read
    path already returns.

    A missing file still yields ``None``: the manifest entry is what makes a ticket
    editable, so an absent body file is a create-like edit, not a failure. Only the
    ticket id reaches the raised error, so no filesystem path leaks.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise TicketFileUnreadable(ticket_id) from exc


def _find_entry_index(entries: list[dict[str, Any]], ticket_id: str) -> int | None:
    """Return the index of the manifest entry whose ``id`` equals ``ticket_id``."""
    for index, entry in enumerate(entries):
        if isinstance(entry, dict) and entry.get("id") == ticket_id:
            return index
    return None


# --------------------------------------------------------------------------- #
# Manifest (tickets.json) serialization
# --------------------------------------------------------------------------- #


def _serialize_manifest(manifest_obj: Mapping[str, Any]) -> str:
    """Serialize the full manifest object to the fixture's on-disk format.

    2-space indent plus a trailing newline, matching how the App Factory writes
    ``tickets.json`` so a rendered manifest diffs cleanly against the original.
    ``ensure_ascii=False`` keeps non-ASCII characters (e.g. ``—``/``→``) verbatim
    — the factory writes raw UTF-8, so escaping them here would make every rendered
    manifest diff against the original on characters the user never touched (and
    rewrite them to ``\\uXXXX`` on apply).
    """
    return json.dumps(manifest_obj, indent=2, ensure_ascii=False) + "\n"


def _draft_to_entry(draft: TicketDraft, content_relpath: str) -> dict[str, Any]:
    """Build the manifest INDEX entry for a newly created ticket.

    ``provides`` is stored as the scalar string the manifest schema uses (not the
    model's ``list[str]`` read-side shape); ``status`` starts at ``todo``. The
    dependency list uses the PRODUCER's key (:data:`DEPENDS_ON_KEYS` [0]) so a
    console-created ticket reads back the way a factory-created one does.

    ``path`` is written EXPLICITLY rather than left to the reader's flat fallback.
    The fallback derives ``<ticketsDir>/<id>.md``, so a v3 ticket that relied on it
    would be looked for under the wrong suffix — and more to the point, a manifest
    that says where its content lives is a manifest nothing has to guess about. It
    is also what makes the created ticket findable by the factory, whose reader takes
    the declared path and has no fallback of this shape at all.

    ``files`` is NOT written. v3's index has no such key: the same information lives
    in the content file's ``critical_files``, which is where the overlap filter reads
    it from. Writing both would create two answers to one question, and the console
    would be the thing that disagreed with itself first.
    """
    return _write_depends_on(
        {
            "id": draft.id,
            "title": draft.title,
            "status": _DEFAULT_STATUS,
            "track": draft.track,
            "milestone": draft.milestone,
            "provides": draft.provides,
            "path": content_relpath,
        },
        list(draft.dependsOn),
    )


_MANIFEST_MIRRORED_KEYS = ("title", "track", "milestone", "dependsOn", "provides")
"""The INDEX fields an edit OWNS — the ones it overwrites in ``tickets.json``.

``files`` left this list when the write path moved to v3 content files. An edit's
``criticalFiles`` is now written to the ticket's own ``critical_files``, and a legacy
``files`` key still sitting in an index entry is left untouched rather than updated:
the reader already prefers the content file's answer, so rewriting a key v3 does not
define would be maintaining a second copy for no reader.
"""


def _edit_mirror(edit: TicketEdit) -> dict[str, Any]:
    """The edit's :data:`_MANIFEST_MIRRORED_KEYS` values, in their stored shapes.

    ``provides`` stays the scalar-string manifest shape; ``dependsOn`` is copied to a
    plain list so no caller shares the model's sequence.

    ``dependsOn`` is spelled camelCase here and is not necessarily the manifest's key —
    :func:`_write_depends_on` decides that after this mapping is overlaid, and removes
    this one on the way through.
    """
    return {
        "title": edit.title,
        "track": edit.track,
        "milestone": edit.milestone,
        "dependsOn": list(edit.dependsOn),
        "provides": edit.provides,
    }


def _merge_edit(existing: Mapping[str, Any], edit: TicketEdit) -> dict[str, Any]:
    """Overlay an edit's fields onto the EXISTING raw manifest entry.

    Starts from a copy of the existing entry so unknown fields (e.g. ``estimate``)
    and the entry's ``id`` / ``status`` survive; only the editable fields
    (:func:`_edit_mirror`) are overwritten — with the dependency list written under
    whichever key this entry already uses (:func:`_write_depends_on`).
    """
    return _write_depends_on({**existing, **_edit_mirror(edit)}, list(edit.dependsOn))


def _write_depends_on(entry: dict[str, Any], depends_on: list[str]) -> dict[str, Any]:
    """Store ``depends_on`` under the ONE key this entry uses, dropping the other.

    An entry must never carry both spellings: the reader resolves
    :data:`~factory_console.file_adapter.manifest.DEPENDS_ON_KEYS` in order, so a
    leftover ``depends_on`` beside a freshly written ``dependsOn`` means the edit
    is read back as the value it replaced. An entry already carrying a key keeps
    it (the factory's ``depends_on``, or a hand-written ``dependsOn``); an entry
    with neither gets the producer's spelling, which is what the factory writes
    and therefore what the rest of the manifest will look like.
    """
    existing_key = next((key for key in DEPENDS_ON_KEYS if key in entry), None)
    for key in DEPENDS_ON_KEYS:
        entry.pop(key, None)
    entry[existing_key or DEPENDS_ON_KEYS[0]] = depends_on
    return entry


# --------------------------------------------------------------------------- #
# Ticket CONTENT (App Factory v3 JSON) rendering
# --------------------------------------------------------------------------- #

_CONTENT_SUFFIX = ".json"
"""The suffix every ticket this console CREATES is written under.

The console writes v3 tickets and only v3 tickets. Reading still accepts Markdown
(:mod:`~factory_console.file_adapter.ticket_content`), because a project mid-migration
must stay viewable; writing does not, because the write DTOs no longer carry a field
that could express a Markdown body. An edit that lands on one is refused with the
migration command rather than converted in place — see :func:`_require_writable_format`.
"""


def _render_ticket_json(ticket_id: str, fields: TicketContentFields) -> str:
    """Render the five structured fields as a v3 ticket content file.

    Key order is ``fac_ticket_md_to_json``'s (App Factory ``lib/ticket.sh``) and the
    schema's: ``id``, ``context``, ``approach``, ``critical_files``, ``interface_data``,
    ``verification``. Two producers writing the same document with different key orders
    make every factory-written ticket diff against every console-written one on the next
    edit, on bytes nobody changed — the same reason
    :func:`_serialize_manifest` matches the factory's manifest formatting rather than
    Python's defaults. ``indent=2`` and the trailing newline match ``jq``'s output for
    the same reason, and ``ensure_ascii=False`` keeps non-ASCII prose verbatim.

    ``notes`` is OMITTED when empty rather than written as ``null`` or ``""``. The
    schema makes it optional, and a key present-and-empty is a different document from
    a key absent — it would show as an added line in the diff of every ticket that has
    no notes, and it claims the planner answered a question they did not.

    **The result is validated before it is returned.** ``parse_ticket_content`` is the
    reader's own validation, so a document this function builds is proven to be one the
    console — and therefore the factory, whose schema it mirrors — will accept. The DTO
    already constrains every field, which is what makes this a belt-and-braces check
    rather than the primary gate: it catches a rendering bug, not bad input, and a
    rendering bug is exactly the class of defect that would otherwise reach disk.
    """
    payload: dict[str, Any] = {
        "id": ticket_id,
        "context": fields.context,
        "approach": fields.approach,
        "critical_files": list(fields.criticalFiles),
        "interface_data": fields.interfaceData,
        "verification": {"commands": list(fields.verificationCommands)},
    }
    if fields.verificationNotes:
        payload["verification"]["notes"] = fields.verificationNotes
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    parse_ticket_content(ticket_id, text)
    return text


def _require_writable_format(ticket_id: str, path: Path) -> None:
    """Refuse to write a ticket whose content file is not a v3 JSON document.

    Raises :class:`~factory_console.file_adapter.ticket_content.TicketFormatRetired`
    for a ``.md`` ticket, naming ``factory-ticket migrate`` — the command that converts
    it — and :class:`~factory_console.file_adapter.ticket_content.TicketFormatUnsupported`
    for a suffix this console has no reader for at all.

    **Refusing beats converting**, and the alternative was tempting: an edit arrives
    with all five structured fields, so the console COULD write them as JSON and repoint
    the manifest. That would silently change a file's format under a user who asked to
    change its text, and it would drop whatever the Markdown carried that the five fields
    do not — front-matter keys, prose belonging to no section. The factory's own migrator
    refuses to guess for exactly this reason: it reports what it cannot parse and writes
    nothing. A refusal naming the command is recoverable in one step; a lossy conversion
    nobody asked for is not recoverable at all.

    It also beats refusing at the DTO. A request for a ``.md`` ticket is well-formed —
    the client sent five valid fields — so the problem is the STATE of the repository,
    not the request, which is why this is a 409 and not a 422.
    """
    suffix = path.suffix.lower()
    if suffix == _CONTENT_SUFFIX:
        return
    if suffix == ".md":
        raise TicketFormatRetired(ticket_id)
    raise TicketFormatUnsupported(ticket_id, path.suffix)


def _content_path_for_create(project: Project, ticket_id: str) -> Path:
    """Where a NEWLY created ticket's content file goes: ``<ticketsDir>/<id>.json``.

    Flat, and deliberately not filed under a ``<milestone>/`` directory with a slug in
    the name the way the factory's planner does. That layout is the PLANNER's, and its
    slug rule is not stated anywhere this console can read — inventing one would produce
    filenames that look like the factory's and are not, which is worse than an obviously
    different shape. Nothing has to guess either way, because the manifest entry declares
    the path (:func:`_draft_to_entry`).
    """
    return resolve_ticket_path(
        project.rootPath,
        project.ticketsDir,
        ticket_id,
        project.ticketsDir / f"{ticket_id}{_CONTENT_SUFFIX}",
    )


# --------------------------------------------------------------------------- #
# Roadmap (ROADMAP.md) rendering
# --------------------------------------------------------------------------- #


def _roadmap_label(ticket_id: str, title: str) -> str:
    """The canonical roadmap item label: a bold id, an em dash, then the title."""
    return f"**{ticket_id}** — {title}"


def _heading_matches(heading_text: str, milestone: str) -> bool:
    """True if a ``## `` heading names ``milestone`` (tolerantly).

    Roadmap headings are often richer than the bare milestone field — e.g.
    milestone ``"MVP"`` under a heading ``"## MVP — make ranger reports usable"``.
    A heading matches when its stripped text equals ``milestone`` OR begins with
    ``milestone`` followed by a separator: a space, an em dash, or a colon (the
    ``"M -"`` case is subsumed by the space prefix).
    """
    stripped = heading_text.strip()
    if stripped == milestone:
        return True
    return any(stripped.startswith(milestone + sep) for sep in (" ", "—", ":"))


def _find_matching_heading(lines: list[str], milestone: str) -> int | None:
    """Return the index of the first ``## `` heading matching ``milestone``."""
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(_H2_PREFIX) and _heading_matches(
            stripped[len(_H2_PREFIX) :], milestone
        ):
            return index
    return None


def _heading_index_for_line(lines: list[str], line_index: int) -> int | None:
    """Return the index of the ``## `` heading whose section contains ``line_index``.

    Walks backward from ``line_index`` to the nearest preceding milestone heading;
    ``None`` when the line precedes every ``## `` heading (e.g. a preamble bullet).
    """
    for index in range(line_index, -1, -1):
        if lines[index].strip().startswith(_H2_PREFIX):
            return index
    return None


def _section_body_bounds(lines: list[str], heading_index: int) -> tuple[int, int]:
    """Return ``[start, end)`` line bounds of a section's body after its heading.

    The body runs from the line after ``heading_index`` up to (excluding) the next
    ``## `` heading, or the end of the file when none follows.
    """
    start = heading_index + 1
    for index in range(start, len(lines)):
        if lines[index].strip().startswith(_H2_PREFIX):
            return start, index
    return start, len(lines)


def _section_insert_index(lines: list[str], body_start: int, body_end: int) -> int:
    """Pick where to insert a new list item within a section's body.

    Appends after the section's LAST existing list item so the new line joins the
    list block. When the section has no list items, inserts at the section's end,
    skipping trailing blank lines so the item does not land after a gap.
    """
    insert_at: int | None = None
    for index in range(body_start, body_end):
        if _LIST_ITEM_RE.match(lines[index].strip()):
            insert_at = index + 1
    if insert_at is not None:
        return insert_at
    end = body_end
    while end > body_start and lines[end - 1].strip() == "":
        end -= 1
    return end


def _rebuild_item_line(line: str, ticket_id: str, title: str) -> str:
    """Rewrite a roadmap item's label IN PLACE, keeping its structural prefix.

    Preserves the original indentation, bullet marker, and checkbox (done-state)
    and replaces only the label text — so editing a ticket's title never resets
    its roadmap checkbox.
    """
    prefix_match = _ITEM_PREFIX_RE.match(line)
    assert prefix_match is not None  # caller only passes matched list-item lines
    indent = prefix_match.group("indent")
    bullet = prefix_match.group("bullet")
    checkbox = prefix_match.group("checkbox") or ""
    return f"{indent}{bullet}{checkbox}{_roadmap_label(ticket_id, title)}"


def _roadmap_create_text(
    current: str, milestone: str | None, ticket_id: str, title: str
) -> str | None:
    """Return the roadmap text with a new item inserted, or ``None`` if no match.

    ``None`` when the ticket has no milestone or no ``## `` section matches it —
    the caller then simply omits the roadmap change.
    """
    if milestone is None:
        return None
    lines = current.split("\n")
    heading_index = _find_matching_heading(lines, milestone)
    if heading_index is None:
        return None
    body_start, body_end = _section_body_bounds(lines, heading_index)
    insert_at = _section_insert_index(lines, body_start, body_end)
    lines.insert(insert_at, f"- [ ] {_roadmap_label(ticket_id, title)}")
    return "\n".join(lines)


def _roadmap_edit_text(
    current: str, ticket_id: str, milestone: str | None, title: str
) -> str | None:
    """Return the roadmap text reflecting an edit to ``ticket_id``, else ``None``.

    Relabels the ticket's existing item in place, but when the edit moves the ticket
    to a DIFFERENT milestone whose ``## `` section exists, removes the old line and
    re-inserts it under the new section — so the roadmap tracks the manifest's
    ``milestone`` (which :func:`_merge_edit` updates) instead of silently keeping the
    line under its old heading. A cross-section move re-lists the item as ``- [ ]``,
    matching how :func:`_roadmap_create_text` first lists a ticket (a moved ticket
    is a fresh entry under its new milestone). When the new milestone has no matching
    section, the line is relabelled in place rather than lost, mirroring create's
    "no matching section → skip the roadmap" tolerance.

    ``None`` when no list item carries ``ticket_id`` — the caller then omits the
    roadmap change.
    """
    lines = current.split("\n")
    item_index = next(
        (
            index
            for index, line in enumerate(lines)
            if _LIST_ITEM_RE.match(line.strip()) and _extract_ticket_id(line) == ticket_id
        ),
        None,
    )
    if item_index is None:
        return None

    heading_index = _heading_index_for_line(lines, item_index)
    # A None milestone can't target a section; otherwise the item is already where
    # it belongs only when a heading precedes it AND that heading names the
    # milestone. An item with no preceding heading (preamble) but a real milestone
    # section elsewhere is NOT in its target section, so it moves too.
    in_target_section = milestone is None or (
        heading_index is not None
        and _heading_matches(lines[heading_index].strip()[len(_H2_PREFIX) :], milestone)
    )
    if not in_target_section:
        without_item = lines[:item_index] + lines[item_index + 1 :]
        moved = _roadmap_create_text("\n".join(without_item), milestone, ticket_id, title)
        if moved is not None:
            return moved

    lines[item_index] = _rebuild_item_line(lines[item_index], ticket_id, title)
    return "\n".join(lines)


def _roadmap_delete_text(current: str, ticket_id: str) -> str | None:
    """Return the roadmap text with ``ticket_id``'s item removed, else ``None``.

    ``None`` when no list item carries ``ticket_id`` — the caller omits the change.
    """
    lines = current.split("\n")
    for index, line in enumerate(lines):
        if _LIST_ITEM_RE.match(line.strip()) and _extract_ticket_id(line) == ticket_id:
            del lines[index]
            return "\n".join(lines)
    return None


def _roadmap_change(
    project: Project, transform: Callable[[str], str | None]
) -> PlannedChange | None:
    """Build the roadmap :class:`PlannedChange`, or ``None`` when nothing changes.

    Skips entirely when the project has no roadmap, the roadmap is unreadable, the
    ``transform`` finds nothing to change (returns ``None``), or the transform is a
    no-op (identical text) — so a roadmap change is emitted only when the three-file
    coupling actually has a matching section/line to touch.
    """
    if project.roadmapPath is None:
        return None
    current = _read_text_or_none(project.roadmapPath)
    if current is None:
        return None
    new_text = transform(current)
    if new_text is None or new_text == current:
        return None
    return PlannedChange(
        path=project.roadmapPath,
        relPath=_rel_posix(project.roadmapPath, project.rootPath),
        currentText=current,
        newText=new_text,
    )


# --------------------------------------------------------------------------- #
# Public render functions
# --------------------------------------------------------------------------- #


def render_create(project: Project, draft: TicketDraft) -> list[PlannedChange]:
    """Compute the planned changes for creating ``draft`` as a new ticket.

    Raises :class:`PathTraversal` if the id is unsafe and
    :class:`TicketAlreadyExists` (409) if it is already in the manifest. Returns
    the manifest change (new entry appended, DECLARING the content path), the new
    ``.json`` content change (``currentText=None``), and — when a ``## <milestone>``
    section matches — the roadmap change with a new ``- [ ] **<id>** — <title>`` line.
    """
    content_path = _content_path_for_create(project, draft.id)
    manifest_path = project.ticketsManifestPath
    document = load_manifest_document(manifest_path)
    if _find_entry_index(document.tickets, draft.id) is not None:
        raise TicketAlreadyExists(draft.id)

    manifest_raw, manifest_obj = document.rawText, document.obj
    manifest_obj["tickets"].append(
        _draft_to_entry(draft, _rel_posix(content_path, project.rootPath))
    )

    changes = [
        PlannedChange(
            path=manifest_path,
            relPath=_rel_posix(manifest_path, project.rootPath),
            currentText=manifest_raw,
            newText=_serialize_manifest(manifest_obj),
        ),
        PlannedChange(
            path=content_path,
            relPath=_rel_posix(content_path, project.rootPath),
            # Read the current text (normally None — the id is new to the manifest)
            # so an orphan <id>.json on disk that is absent from the manifest surfaces
            # in the diff as a modify instead of being silently clobbered, matching
            # the edit/delete siblings.
            currentText=_read_text_or_none(content_path),
            newText=_render_ticket_json(draft.id, draft),
        ),
    ]
    roadmap_change = _roadmap_change(
        project,
        lambda current: _roadmap_create_text(current, draft.milestone, draft.id, draft.title),
    )
    if roadmap_change is not None:
        changes.append(roadmap_change)
    return changes


def render_edit(project: Project, ticket_id: str, edit: TicketEdit) -> list[PlannedChange]:
    """Compute the planned changes for editing ticket ``ticket_id``.

    Raises :class:`PathTraversal` if the id is unsafe, :class:`UnknownTicket` (404)
    if it is absent, ``TicketFormatRetired`` (409) if its content file is a ``.md``
    this console can no longer write, and :class:`TicketFileUnreadable` (500) if the
    content file exists but cannot be read.

    The two halves are treated DIFFERENTLY, and deliberately. The manifest entry is
    MERGED (:func:`_merge_edit`), so unknown index fields — ``estimate``, a legacy
    ``files`` — and the entry's ``id`` / ``status`` survive. The content file is
    REPLACED: a v3 ticket's schema forbids extra keys, so there is nothing in it a
    merge could preserve that the five supplied fields do not already say, and every
    field is required, so a merge could not be partial either. Preserving unknown keys
    there would mean preserving keys the factory itself rejects.

    The current content text is still read — for the diff's ``currentText``, and so an
    unreadable file fails closed rather than being overwritten sight unseen.
    """
    # Validate the id BEFORE the manifest is read. An unsafe id must raise
    # PathTraversal (400), not the UnknownTicket (404) a manifest miss yields — and
    # since the .md path now comes from the entry, nothing else would check the id
    # on the way to that miss.
    _require_safe_id(ticket_id)
    manifest_path = project.ticketsManifestPath
    document = load_manifest_document(manifest_path)
    index = _find_entry_index(document.tickets, ticket_id)
    if index is None:
        raise UnknownTicket(ticket_id)

    # The ENTRY says where the content file is, so it is read before the entry is
    # overwritten — and from the same document the index came from.
    content_path = _entry_content_path(project, document.tickets[index], ticket_id)
    # Before anything is computed: a format this console cannot write is refused with
    # the migration command, not converted in place. Checked here rather than at the
    # DTO because the request is well-formed — it is the repository that is not v3 yet.
    _require_writable_format(ticket_id, content_path)
    manifest_raw, manifest_obj = document.rawText, document.obj
    manifest_obj["tickets"][index] = _merge_edit(manifest_obj["tickets"][index], edit)

    changes = [
        PlannedChange(
            path=manifest_path,
            relPath=_rel_posix(manifest_path, project.rootPath),
            currentText=manifest_raw,
            newText=_serialize_manifest(manifest_obj),
        ),
        PlannedChange(
            path=content_path,
            relPath=_rel_posix(content_path, project.rootPath),
            currentText=_read_content_or_none(ticket_id, content_path),
            newText=_render_ticket_json(ticket_id, edit),
        ),
    ]
    roadmap_change = _roadmap_change(
        project,
        lambda current: _roadmap_edit_text(current, ticket_id, edit.milestone, edit.title),
    )
    if roadmap_change is not None:
        changes.append(roadmap_change)
    return changes


def render_delete(project: Project, ticket_id: str) -> list[PlannedChange]:
    """Compute the planned changes for deleting ticket ``ticket_id``.

    Raises :class:`PathTraversal` if the id is unsafe and :class:`UnknownTicket`
    (404) if it is absent. Removes the manifest entry, marks the content-file change
    ``newText=None`` (delete the file), and — when a roadmap item carries the id —
    removes that item line.

    Deleting is NOT gated on the content format, unlike editing. An edit has to produce
    a document in a format this console can write; a delete removes the file whatever it
    holds, and refusing would leave a ``.md`` ticket undeletable through the very UI that
    lists it — a lockout with no remedy short of hand-editing the manifest.
    """
    _require_safe_id(ticket_id)  # PathTraversal (400) before the manifest's 404
    manifest_path = project.ticketsManifestPath
    document = load_manifest_document(manifest_path)
    index = _find_entry_index(document.tickets, ticket_id)
    if index is None:
        raise UnknownTicket(ticket_id)

    content_path = _entry_content_path(project, document.tickets[index], ticket_id)
    manifest_raw, manifest_obj = document.rawText, document.obj
    del manifest_obj["tickets"][index]

    changes = [
        PlannedChange(
            path=manifest_path,
            relPath=_rel_posix(manifest_path, project.rootPath),
            currentText=manifest_raw,
            newText=_serialize_manifest(manifest_obj),
        ),
        PlannedChange(
            path=content_path,
            relPath=_rel_posix(content_path, project.rootPath),
            currentText=_read_text_or_none(content_path),
            newText=None,
        ),
    ]
    roadmap_change = _roadmap_change(
        project, lambda current: _roadmap_delete_text(current, ticket_id)
    )
    if roadmap_change is not None:
        changes.append(roadmap_change)
    return changes
