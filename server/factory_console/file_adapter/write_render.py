"""Pure write-render: compute the DESIRED text of the three coupled files.

Given a project's CURRENT on-disk state plus a validated create/edit/delete, this
module computes exactly what each of ``docs/planning/tickets.json``, the ticket
``<id>.md``, and ``ROADMAP.md`` should contain — as a set of
:class:`PlannedChange` — WITHOUT writing anything. Kept pure so the dry-run diff
engine and the atomic co-writer consume the identical planned change-set and can
never disagree about what would change.

Forward-compatibility mirrors the read side: an edit MERGES onto the existing raw
manifest entry, so unknown fields (e.g. ``estimate``) survive verbatim — the same
tolerance the read path keeps on :attr:`Ticket.raw`. Path safety is
defense-in-depth: every ticket id is re-validated against
:data:`TICKET_ID_PATTERN` and resolved under ``project.rootPath`` via
:func:`_safe_resolve` (mirroring :mod:`~factory_console.file_adapter.ticket_md`),
so a slash/``..`` id can never escape the tickets directory.

Only the THREE known relative paths are ever emitted — the manifest, the ticket
``.md``, and the roadmap — never a run-state path. Nothing here writes, makes
directories, or has any side effect; it only reads.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from factory_console.domain.project import Project
from factory_console.domain.ticket import TICKET_ID_PATTERN
from factory_console.domain.write import TicketDraft, TicketEdit
from factory_console.errors import FactoryConsoleError
from factory_console.file_adapter.manifest import load_manifest
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.roadmap_parse import _LIST_ITEM_RE, _extract_ticket_id

_TICKET_ID_RE = re.compile(TICKET_ID_PATTERN)
"""The canonical ticket-id pattern compiled once at import for re-validation."""

_ID_ESCAPES_ROOT = "Ticket id resolves outside the project root"

_FENCE = "---"
"""A front-matter fence line — exactly three dashes on their own line."""

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


def _safe_resolve(project: Project, ticket_id: str) -> Path:
    """Resolve ``<ticketsDir>/<ticket_id>.md``, refusing any unsafe id.

    Mirrors :func:`factory_console.file_adapter.ticket_md._safe_resolve`: raises
    :class:`PathTraversal` when the id fails :data:`TICKET_ID_PATTERN` or when the
    resolved path is not contained under ``project.rootPath``. Both sides of the
    containment check are resolved so a symlinked temp root (``/tmp`` ->
    ``/var/folders`` on macOS) does not cause a false negative.
    """
    if _TICKET_ID_RE.fullmatch(ticket_id) is None:
        raise PathTraversal.from_pattern_violation(ticket_id)
    candidate = (project.ticketsDir / f"{ticket_id}.md").resolve(strict=False)
    if not candidate.is_relative_to(project.rootPath.resolve()):
        raise PathTraversal(ticket_id, reason=_ID_ESCAPES_ROOT)
    return candidate


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


def _read_manifest_source(path: Path) -> tuple[str, dict[str, Any]]:
    """Return the manifest's raw text and its parsed full object.

    :func:`load_manifest` intentionally drops the manifest's top-level keys
    (``project``, ``schemaVersion``), returning only the tickets list — so to
    re-serialize the WHOLE object with those keys preserved we re-read the raw
    JSON here. ``load_manifest`` has already validated (valid UTF-8, valid JSON,
    a dict carrying a ``tickets`` list) at every call site before this runs, so
    ``read_text`` / ``json.loads`` will not raise on structure.
    """
    raw_text = path.read_text(encoding="utf-8")
    return raw_text, json.loads(raw_text)


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


def _draft_to_entry(draft: TicketDraft) -> dict[str, Any]:
    """Build the camelCase manifest entry for a newly created ticket.

    ``provides`` is stored as the scalar string the manifest schema uses (not the
    model's ``list[str]`` read-side shape); ``status`` starts at ``todo``.
    """
    return {
        "id": draft.id,
        "title": draft.title,
        "status": _DEFAULT_STATUS,
        "track": draft.track,
        "milestone": draft.milestone,
        "dependsOn": list(draft.dependsOn),
        "provides": draft.provides,
        "files": list(draft.files),
    }


def _merge_edit(existing: Mapping[str, Any], edit: TicketEdit) -> dict[str, Any]:
    """Overlay an edit's fields onto the EXISTING raw manifest entry.

    Starts from a copy of the existing entry so unknown fields (e.g. ``estimate``)
    and the entry's ``id`` / ``status`` survive; only the editable fields are
    overwritten. ``provides`` stays the scalar-string manifest shape.
    """
    merged = dict(existing)
    merged["title"] = edit.title
    merged["track"] = edit.track
    merged["milestone"] = edit.milestone
    merged["dependsOn"] = list(edit.dependsOn)
    merged["provides"] = edit.provides
    merged["files"] = list(edit.files)
    return merged


# --------------------------------------------------------------------------- #
# Ticket .md rendering
# --------------------------------------------------------------------------- #


def _render_md(front_matter: Mapping[str, Any], body_markdown: str) -> str:
    """Render a ticket ``.md`` from optional YAML front-matter plus the body.

    When ``front_matter`` is non-empty, emit a ``---`` fenced YAML block
    (``sort_keys=False`` to keep author order) followed by the body — round-trip
    consistent with :func:`~factory_console.file_adapter.ticket_md.read_ticket_md`.
    When it is empty, emit just the body with no fence. ``allow_unicode=True``
    keeps non-ASCII front-matter values verbatim, matching the raw-UTF-8 the body
    and manifest are rendered with (see :func:`_serialize_manifest`) so no coupled
    file escapes characters the user never touched.
    """
    if not front_matter:
        return body_markdown
    yaml_block = yaml.safe_dump(dict(front_matter), sort_keys=False, allow_unicode=True)
    return f"{_FENCE}\n{yaml_block}{_FENCE}\n{body_markdown}"


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


def _roadmap_edit_text(current: str, ticket_id: str, title: str) -> str | None:
    """Return the roadmap text with ``ticket_id``'s item relabelled, else ``None``.

    ``None`` when no list item carries ``ticket_id`` — the caller omits the change.
    """
    lines = current.split("\n")
    for index, line in enumerate(lines):
        if _LIST_ITEM_RE.match(line.strip()) and _extract_ticket_id(line) == ticket_id:
            lines[index] = _rebuild_item_line(line, ticket_id, title)
            return "\n".join(lines)
    return None


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
    the manifest change (new entry appended), the new ``.md`` change
    (``currentText=None``), and — when a ``## <milestone>`` section matches — the
    roadmap change with a new ``- [ ] **<id>** — <title>`` line.
    """
    md_path = _safe_resolve(project, draft.id)
    manifest_path = project.ticketsManifestPath
    _schema_version, entries = load_manifest(manifest_path)
    if _find_entry_index(entries, draft.id) is not None:
        raise TicketAlreadyExists(draft.id)

    manifest_raw, manifest_obj = _read_manifest_source(manifest_path)
    manifest_obj["tickets"].append(_draft_to_entry(draft))

    changes = [
        PlannedChange(
            path=manifest_path,
            relPath=_rel_posix(manifest_path, project.rootPath),
            currentText=manifest_raw,
            newText=_serialize_manifest(manifest_obj),
        ),
        PlannedChange(
            path=md_path,
            relPath=_rel_posix(md_path, project.rootPath),
            currentText=None,
            newText=_render_md(draft.frontMatter, draft.bodyMarkdown),
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

    Raises :class:`PathTraversal` if the id is unsafe and :class:`UnknownTicket`
    (404) if it is absent. MERGES the edit onto the existing raw manifest entry so
    unknown fields survive, re-renders the ``.md`` against its current on-disk
    text, and — when a roadmap item carries the id — relabels that item in place.
    """
    md_path = _safe_resolve(project, ticket_id)
    manifest_path = project.ticketsManifestPath
    _schema_version, entries = load_manifest(manifest_path)
    index = _find_entry_index(entries, ticket_id)
    if index is None:
        raise UnknownTicket(ticket_id)

    manifest_raw, manifest_obj = _read_manifest_source(manifest_path)
    manifest_obj["tickets"][index] = _merge_edit(manifest_obj["tickets"][index], edit)

    changes = [
        PlannedChange(
            path=manifest_path,
            relPath=_rel_posix(manifest_path, project.rootPath),
            currentText=manifest_raw,
            newText=_serialize_manifest(manifest_obj),
        ),
        PlannedChange(
            path=md_path,
            relPath=_rel_posix(md_path, project.rootPath),
            currentText=_read_text_or_none(md_path),
            newText=_render_md(edit.frontMatter, edit.bodyMarkdown),
        ),
    ]
    roadmap_change = _roadmap_change(
        project, lambda current: _roadmap_edit_text(current, ticket_id, edit.title)
    )
    if roadmap_change is not None:
        changes.append(roadmap_change)
    return changes


def render_delete(project: Project, ticket_id: str) -> list[PlannedChange]:
    """Compute the planned changes for deleting ticket ``ticket_id``.

    Raises :class:`PathTraversal` if the id is unsafe and :class:`UnknownTicket`
    (404) if it is absent. Removes the manifest entry, marks the ``.md`` change
    ``newText=None`` (delete the file), and — when a roadmap item carries the id —
    removes that item line.
    """
    md_path = _safe_resolve(project, ticket_id)
    manifest_path = project.ticketsManifestPath
    _schema_version, entries = load_manifest(manifest_path)
    index = _find_entry_index(entries, ticket_id)
    if index is None:
        raise UnknownTicket(ticket_id)

    manifest_raw, manifest_obj = _read_manifest_source(manifest_path)
    del manifest_obj["tickets"][index]

    changes = [
        PlannedChange(
            path=manifest_path,
            relPath=_rel_posix(manifest_path, project.rootPath),
            currentText=manifest_raw,
            newText=_serialize_manifest(manifest_obj),
        ),
        PlannedChange(
            path=md_path,
            relPath=_rel_posix(md_path, project.rootPath),
            currentText=_read_text_or_none(md_path),
            newText=None,
        ),
    ]
    roadmap_change = _roadmap_change(
        project, lambda current: _roadmap_delete_text(current, ticket_id)
    )
    if roadmap_change is not None:
        changes.append(roadmap_change)
    return changes
