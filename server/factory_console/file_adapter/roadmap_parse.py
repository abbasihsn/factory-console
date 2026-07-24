"""Parse a ``ROADMAP.md`` body into structured milestones.

The roadmap document is authored as markdown: a ``## `` heading opens a
milestone and the ``- ``/``* `` list items beneath it are its work items, each
optionally carrying a GitHub-style checkbox and a linked ticket id. This module
turns that prose into the :class:`~factory_console.domain.deps.RoadmapMilestone`
/ :class:`~factory_console.domain.deps.RoadmapItem` breakdown the ``/roadmap``
view renders as a navigable list.

The parser is *total and tolerant*, mirroring the front-matter reader's
forgiveness: it never raises on malformed markdown, it skips lines it cannot
make sense of, and a body with no ``## `` headings yields ``[]``.
"""

from __future__ import annotations

import re

from factory_console.domain import TICKET_ID_PATTERN
from factory_console.domain.deps import RoadmapItem, RoadmapMilestone

# A milestone opener is an h2 (``## ``) heading only — the ``# `` document title
# and any ``### `` sub-heading are deliberately NOT milestones. Note ``### `` does
# not itself satisfy ``startswith("## ")`` (its third character is ``#``, not a
# space), so a plain h2 prefix check is exact.
_H2_PREFIX = "## "

# A list item is a ``- `` or ``* `` bullet (after leading whitespace is stripped).
_LIST_ITEM_RE = re.compile(r"^[-*]\s+(?P<rest>.*)$")

# A leading GitHub-style checkbox: ``[x]``/``[X]`` (done), ``[ ]`` (not done).
_CHECKBOX_RE = re.compile(r"^\[(?P<mark>[ xX])\]\s*(?P<rest>.*)$")

# Id candidates scanned left-to-right across an item line: a no-space bold span
# (``**T01**``) or a parenthesized group (``(CAD-100)``). The first candidate
# whose content looks like a ticket id (see :func:`_looks_like_ticket_id`) wins.
_ID_CANDIDATE_RE = re.compile(r"\*\*(?P<bold>[^*]+)\*\*|\((?P<paren>[^)]+)\)")


def _looks_like_ticket_id(token: str) -> bool:
    """True if ``token`` is shaped like a ticket id (e.g. ``T01``, ``CAD-100``).

    The token must match :data:`TICKET_ID_PATTERN` in full — reusing the single
    source of truth for id characters rather than a divergent copy — AND contain
    at least one digit. The digit requirement is what separates a real id from a
    prose bold label: ``**Weekly digest**`` is rejected by the pattern (it has
    internal whitespace), while a single-word label like ``**Reminders**`` passes
    the pattern yet is not an id — every factory/project ticket id carries a
    numeric component (``T01``, ``CAD-100``, ``TM-001``), so requiring a digit
    keeps section labels from being mistaken for ids.
    """
    return re.fullmatch(TICKET_ID_PATTERN, token) is not None and any(
        char.isdigit() for char in token
    )


def _extract_ticket_id(line: str) -> str | None:
    """Return the first id-shaped token on ``line``, parenthesized or bold, else ``None``.

    Candidates are scanned strictly left-to-right so that when both a bold span
    and a parenthesized group appear, the earlier one is preferred; a candidate
    that is not id-shaped (a prose bold label, a non-id parenthetical) is skipped
    and scanning continues.
    """
    for match in _ID_CANDIDATE_RE.finditer(line):
        token = (match.group("bold") or match.group("paren") or "").strip()
        if _looks_like_ticket_id(token):
            return token
    return None


def _parse_item(rest: str) -> RoadmapItem:
    """Build a :class:`RoadmapItem` from a list item's post-marker text.

    ``rest`` is the item text with its ``- ``/``* `` marker already removed. The
    leading checkbox token (if any) sets ``done`` and is stripped from ``text``;
    everything else — including any bold markers and the ticket id — is preserved
    in ``text`` so the label stays readable. ``ticketId`` is scanned from the
    ORIGINAL ``rest`` (before the checkbox strip is immaterial — the checkbox is
    never id-shaped) via :func:`_extract_ticket_id`.
    """
    done: bool | None = None
    text = rest
    checkbox = _CHECKBOX_RE.match(rest)
    if checkbox is not None:
        done = checkbox.group("mark") in ("x", "X")
        text = checkbox.group("rest")
    return RoadmapItem(text=text.strip(), ticketId=_extract_ticket_id(rest), done=done)


def parse_milestones(body_markdown: str) -> list[RoadmapMilestone]:
    """Parse ``body_markdown`` into an ordered list of :class:`RoadmapMilestone`.

    Walks the body top to bottom:

    - A ``## `` (h2) heading opens a new milestone whose ``name`` is the stripped
      heading text; ``# `` and ``### `` headings are ignored.
    - A ``- ``/``* `` list item beneath the current milestone becomes a
      :class:`RoadmapItem` (checkbox state -> ``done``, first id-shaped token ->
      ``ticketId``, cleaned label -> ``text``). List items appearing before the
      first ``## `` heading have no owning milestone and are dropped.
    - Every other line (prose, blank lines, other headings) is ignored.

    Total and never raises: malformed lines are skipped, and a body with no
    ``## `` headings returns ``[]``.
    """
    milestones: list[RoadmapMilestone] = []
    current_name: str | None = None
    current_items: list[RoadmapItem] = []

    def flush() -> None:
        if current_name is not None:
            milestones.append(RoadmapMilestone(name=current_name, items=current_items))

    for raw_line in body_markdown.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith(_H2_PREFIX):
            flush()
            current_name = stripped[len(_H2_PREFIX) :].strip()
            current_items = []
            continue
        if current_name is None:
            continue
        item_match = _LIST_ITEM_RE.match(stripped)
        if item_match is not None:
            current_items.append(_parse_item(item_match.group("rest")))

    flush()
    return milestones
