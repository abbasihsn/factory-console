# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.
"""Read the factory's per-ticket run artifacts that sit beside ``run-state.json``.

The App Factory leaves four things under ``<project>/.factory/``; T78 already
reads the first, and this module reads the other three:

1. ``run-state.json`` — state + ``pr_url`` per ticket (T78's
   :mod:`~factory_console.file_adapter.run_state`; :func:`read_pr_urls` here
   reuses that ONE parser rather than opening the file a second time).
2. ``results/<ticket_id>.json`` — the lane result, read as the named subset
   :class:`~factory_console.domain.run_record.RunResultSummary`.
3. ``receipts/<ticket_id>.json`` — review receipt, read for PRESENCE ONLY.
4. ``last-stop.json`` — why the last run stopped.

Every source is INDEPENDENTLY optional: a project can have a run-state and no
receipts, or results and no last-stop, and in a fresh clone all four are absent
because ``.factory/`` is gitignored. So nothing here raises on absence — a
missing or malformed artifact yields ``None``/``False`` and a ``warning`` log,
exactly as :func:`~factory_console.file_adapter.run_state.read_json_run_state`
does. The one exception is an UNSAFE TICKET ID: ids arrive from a URL path
segment and are about to be joined into a path, so they are validated and
rejected with :class:`~factory_console.file_adapter.path_safety.PathTraversal`
BEFORE any filesystem access.

The console MUST NOT write, create, or delete anything here — a guard test
asserts this module contains no filesystem-mutating call.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factory_console.domain import TICKET_ID_PATTERN, RunStateSource
from factory_console.domain.run_record import LastStop, RunResultSummary
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.run_state import read_json_run_state

_LOGGER = logging.getLogger(__name__)

RESULTS_RELATIVE = Path(".factory") / "results"
"""Project-relative location of the per-ticket lane results directory."""

RECEIPTS_RELATIVE = Path(".factory") / "receipts"
"""Project-relative location of the per-ticket review receipts directory."""

LAST_STOP_RELATIVE = Path(".factory") / "last-stop.json"
"""Project-relative location of the "why the last run stopped" file."""


def _require_safe_ticket_id(ticket_id: str) -> str:
    """Return ``ticket_id`` if it is safe to use as ONE path segment, else raise.

    The same rule :func:`~factory_console.file_adapter.run_state.probe_ticket_state`
    applies, for the same reason: ``fullmatch`` (not ``match``) so a trailing
    newline cannot slip past the ``$`` anchor, plus an explicit rejection of bare
    ``.``/``..``, which satisfy :data:`TICKET_ID_PATTERN` (it allows ``.``) yet
    are single-segment traversals.

    Raises:
        PathTraversal: (``invalid_ticket_id``, 400) before any path is joined.
    """
    if re.fullmatch(TICKET_ID_PATTERN, ticket_id) is None:
        raise PathTraversal.from_pattern_violation(ticket_id)
    if ticket_id in (".", ".."):
        raise PathTraversal(ticket_id)
    return ticket_id


def find_results_dir(project_root: Path) -> Path | None:
    """Return ``<project_root>/.factory/results`` if it is a directory, else ``None``."""
    candidate = project_root / RESULTS_RELATIVE
    return candidate if candidate.is_dir() else None


def find_receipts_dir(project_root: Path) -> Path | None:
    """Return ``<project_root>/.factory/receipts`` if it is a directory, else ``None``."""
    candidate = project_root / RECEIPTS_RELATIVE
    return candidate if candidate.is_dir() else None


def find_last_stop_file(project_root: Path) -> Path | None:
    """Return ``<project_root>/.factory/last-stop.json`` if it is a file, else ``None``."""
    candidate = project_root / LAST_STOP_RELATIVE
    return candidate if candidate.is_file() else None


def _load_json_object(path: Path, artifact: str) -> dict[str, Any] | None:
    """Parse ``path`` into a JSON object, or return ``None`` and log why.

    NEVER raises. An artifact written by another process is not a request
    failure: an unreadable file, unparseable JSON, and a document that is not an
    object all degrade to ``None`` with a ``warning``, mirroring
    :func:`~factory_console.file_adapter.run_state.read_json_run_state` (including
    its non-``JSONDecodeError`` guards for pathological input).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        _LOGGER.warning("%s: %s could not be read; treating it as absent", artifact, path)
        return None
    try:
        document = json.loads(raw)
    except (ValueError, RecursionError, MemoryError):
        _LOGGER.warning("%s: %s is not valid JSON; treating it as absent", artifact, path)
        return None
    if not isinstance(document, dict):
        _LOGGER.warning(
            "%s: %s is a %s, not a JSON object; treating it as absent",
            artifact,
            path,
            type(document).__name__,
        )
        return None
    return document


def read_result(project_root: Path, ticket_id: str) -> RunResultSummary | None:
    """Return the lane result for ``ticket_id``, or ``None`` when there is none.

    ``None`` covers every "no answer" case — no ``results`` directory, no file for
    this ticket, an unreadable or non-object file — because the caller reports the
    reason by NAMING the source in ``RunRecord.unavailable`` rather than by
    distinguishing null from null.

    Raises:
        PathTraversal: if ``ticket_id`` is not a single path-safe segment, raised
            BEFORE the results path is joined or probed.
    """
    _require_safe_ticket_id(ticket_id)
    results_dir = find_results_dir(project_root)
    if results_dir is None:
        return None
    path = results_dir / f"{ticket_id}.json"
    if not path.is_file():
        return None
    document = _load_json_object(path, "lane result")
    if document is None:
        return None
    try:
        return RunResultSummary.model_validate(document)
    except ValidationError:
        # A modelled key present with the WRONG type (``review_iterations:
        # "two"``). Rare, but the factory owns this file, so a type change there
        # must not 500 the runs endpoint — degrade to "no result", which the
        # caller reports by naming ``results`` in ``unavailable``.
        _LOGGER.warning(
            "lane result: %s does not match the expected shape; treating it as absent", path
        )
        return None


def has_receipt(project_root: Path, ticket_id: str) -> bool:
    """True if ``.factory/receipts/<ticket_id>.json`` exists as a file.

    PRESENCE ONLY — receipt content is not parsed or modelled anywhere in this
    console (see :class:`~factory_console.domain.run_record.RunRecord`).

    Raises:
        PathTraversal: if ``ticket_id`` is not a single path-safe segment, raised
            BEFORE the receipts path is joined or probed.
    """
    _require_safe_ticket_id(ticket_id)
    receipts_dir = find_receipts_dir(project_root)
    if receipts_dir is None:
        return False
    return (receipts_dir / f"{ticket_id}.json").is_file()


def read_last_stop(project_root: Path) -> LastStop | None:
    """Return the project's :class:`LastStop`, or ``None`` when the file is absent.

    An absent file is ``None``. A file that IS present but unreadable, malformed,
    or carrying no string ``reason`` yields an EMPTY :class:`LastStop` rather than
    ``None``: presence is a fact the caller reports through
    ``sources.lastStop.found``, and collapsing "present but unparseable" into
    "absent" would lose it. Never raises — takes no ticket id, so there is no
    path segment to validate.
    """
    path = find_last_stop_file(project_root)
    if path is None:
        return None
    document = _load_json_object(path, "last-stop")
    if document is None:
        return LastStop()
    try:
        return LastStop.model_validate(document)
    except ValidationError:
        # A ``reason`` that is not a string. The file is still THERE, so the
        # answer stays an empty LastStop rather than ``None``.
        _LOGGER.warning("last-stop: %s carries no usable 'reason'; reporting it empty", path)
        return LastStop()


def read_pr_urls(source: RunStateSource | None) -> dict[str, str]:
    """Return ``{ticket_id: pr_url}`` from the project's run-state source.

    Delegates to T78's :func:`~factory_console.file_adapter.run_state.read_json_run_state`
    so ``run-state.json`` keeps exactly ONE parser — a second reader here would be
    a second authority on the factory's format, free to drift from the first.
    Only the JSON form carries PR urls; a marker-directory source (or no source at
    all) has none, so the answer is empty.
    """
    if source is None or source.kind != "json":
        return {}
    return read_json_run_state(source.path).pr_urls
