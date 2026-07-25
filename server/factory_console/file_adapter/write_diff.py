"""Pure dry-run diff engine: a planned change-set to a unified DiffPreview.

Given the :class:`PlannedChange` set that :mod:`write_render` computes (the desired
text of the three coupled files, without touching disk), this module renders each
change as a unified diff and wraps the set as a :class:`DiffPreview` — exactly what
the UI diff-preview modal and the API dry-run show the user before they confirm.

Shared by both the fake and the real writer so the preview a user sees is computed
by the identical code the writer plans from — no drift between "what preview shows"
and "what apply does". Strictly read-only: it derives text diffs from the
already-in-memory ``currentText``/``newText`` of each change and never reads,
writes, or creates a filesystem path. This is the dry-run guarantee.
"""

from __future__ import annotations

import difflib

from factory_console.domain.write import DiffPreview, FileDiff
from factory_console.file_adapter.write_render import PlannedChange


def preview(ticket_id: str, planned: list[PlannedChange]) -> DiffPreview:
    """Render ``planned`` as a per-file :class:`DiffPreview`, purely in memory.

    For each :class:`PlannedChange`, in input order (manifest, ``.md``, roadmap):

    * a no-op (``currentText == newText``) is OMITTED, so the preview lists only
      genuine changes;
    * ``changeKind`` is ``"create"`` when ``currentText is None``, ``"delete"`` when
      ``newText is None``, else ``"modify"``;
    * ``diff`` is a unified diff (:func:`difflib.unified_diff`) over the current vs
      new text split into lines, with ``from``/``to`` filenames ``a/<relPath>`` and
      ``b/<relPath>``.

    ``lineterm=""`` stops difflib appending newlines to the ``---``/``+++``/``@@``
    header lines (the body lines carry none, since ``splitlines()`` strips endings),
    so joining with ``"\\n"`` yields a clean unified-diff string with no doubled or
    trailing blank lines. Returns a :class:`DiffPreview` keyed by ``ticket_id``,
    preserving the input order of ``planned``.
    """
    files: list[FileDiff] = []
    for change in planned:
        if change.currentText == change.newText:
            continue
        if change.currentText is None:
            change_kind = "create"
        elif change.newText is None:
            change_kind = "delete"
        else:
            change_kind = "modify"
        diff_lines = difflib.unified_diff(
            (change.currentText or "").splitlines(),
            (change.newText or "").splitlines(),
            fromfile=f"a/{change.relPath}",
            tofile=f"b/{change.relPath}",
            lineterm="",
        )
        files.append(
            FileDiff(path=change.relPath, changeKind=change_kind, diff="\n".join(diff_lines))
        )
    return DiffPreview(ticketId=ticket_id, files=files)
