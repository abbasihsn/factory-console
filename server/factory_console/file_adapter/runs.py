# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.
"""Read the factory's per-ticket run artifacts that sit beside ``run-state.json``.

The App Factory leaves four things under ``<project>/.factory/``; T78 already
reads the first, and this module reads the other three:

1. ``run-state.json`` — state + ``pr_url`` per ticket (T78's
   :mod:`~factory_console.file_adapter.run_state`; :func:`read_pr_urls` here
   calls that ONE parser rather than reading the format itself — see its
   docstring for what that does and does not buy).
2. ``results/<ticket_id>.json`` — the lane result, read as the named subset
   :class:`~factory_console.domain.run_record.RunResultSummary`.
3. ``receipts/<ticket_id>.json`` — review receipt, read for PRESENCE ONLY.
4. ``last-stop.json`` — why the last run stopped.

Every source is INDEPENDENTLY optional: a project can have a run-state and no
receipts, or results and no last-stop, and in a fresh clone all four are absent
because ``.factory/`` is gitignored. So nothing here raises on absence — a
missing or malformed artifact yields ``None``/``False`` and a ``warning`` log,
exactly as :func:`~factory_console.file_adapter.run_state.read_json_run_state`
does. :func:`read_last_stop` is the one deliberate exception to the ``None``
half of that rule: a last-stop file that is PRESENT but unreadable yields an
empty :class:`~factory_console.domain.run_record.LastStop`, because presence is
a fact the caller reports and collapsing it into "absent" would lose it.

The other exception is an UNSAFE TICKET ID: ids arrive from a URL path
segment and are about to be joined into a path, so they are validated and
rejected with :class:`~factory_console.file_adapter.path_safety.PathTraversal`
BEFORE any filesystem access. Validating the id bounds the join; :func:`_probe`
separately bounds what the join RESOLVES to, so a symlink cannot walk an
artifact read out of the project root.

The console MUST NOT write, create, or delete anything here — a guard test
asserts this module contains no filesystem-mutating call.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factory_console.domain import RunStateSource
from factory_console.domain.run_record import LastStop, RunResultSummary
from factory_console.file_adapter.path_safety import require_safe_ticket_id_segment
from factory_console.file_adapter.run_state import read_json_run_state

_LOGGER = logging.getLogger(__name__)

RESULTS_RELATIVE = Path(".factory") / "results"
"""Project-relative location of the per-ticket lane results directory."""

RECEIPTS_RELATIVE = Path(".factory") / "receipts"
"""Project-relative location of the per-ticket review receipts directory."""

LAST_STOP_RELATIVE = Path(".factory") / "last-stop.json"
"""Project-relative location of the "why the last run stopped" file."""


def _contained(candidate: Path, project_root: Path) -> bool:
    """True if ``candidate`` really resolves inside ``project_root``.

    Validating the ticket id bounds what the JOIN can produce; it says nothing
    about what the join RESOLVES to. ``is_dir()``/``is_file()`` follow symlinks,
    so a symlinked ``.factory/results`` (or a single symlinked entry inside it)
    would otherwise be read from outside the project root and its contents
    surfaced in a response — and silently, since the endpoint reports the LEXICAL
    path, which still looks in-root. The NFR is that nothing out-of-root reaches
    a response, so containment is checked on the RESOLVED path, mirroring
    :func:`~factory_console.file_adapter.ticket_md._safe_resolve`. Both sides are
    resolved so a symlinked temp root (``/tmp``, macOS ``/var/folders``) is not a
    false negative.
    """
    try:
        return candidate.resolve(strict=False).is_relative_to(project_root.resolve())
    except (OSError, RuntimeError):
        # ``RuntimeError`` and not only ``OSError``: ``resolve(strict=False)``
        # raises ``RuntimeError("Symlink loop from ...")`` for a cyclic link, which
        # is not an ``OSError``. Reaching it needs a loop in a component the
        # is_dir/is_file check did not already walk, but this module's contract is
        # that it never raises for a bad artifact, and an uncaught RuntimeError
        # would 500 the endpoint. Cannot prove containment -> do not read it.
        return False


def _probe(candidate: Path, project_root: Path, artifact: str, *, want_dir: bool) -> Path | None:
    """Return ``candidate`` if it is an in-root directory/file, else ``None``.

    Out-of-root resolution is reported as ABSENT rather than raised: this module
    never raises for an artifact problem (only for an unsafe ticket id), and
    "there is no run data here" is the honest answer for an artifact the console
    is not allowed to read. It is logged so the degradation is attributable.
    """
    if not (candidate.is_dir() if want_dir else candidate.is_file()):
        return None
    if not _contained(candidate, project_root):
        _LOGGER.warning(
            "%s: %s resolves outside the project root; treating it as absent", artifact, candidate
        )
        return None
    return candidate


def find_results_dir(project_root: Path) -> Path | None:
    """Return ``<project_root>/.factory/results`` if it is an in-root directory, else ``None``."""
    return _probe(project_root / RESULTS_RELATIVE, project_root, "results", want_dir=True)


def find_receipts_dir(project_root: Path) -> Path | None:
    """Return ``<project_root>/.factory/receipts`` if it is an in-root directory, else ``None``."""
    return _probe(project_root / RECEIPTS_RELATIVE, project_root, "receipts", want_dir=True)


def find_last_stop_file(project_root: Path) -> Path | None:
    """Return ``<project_root>/.factory/last-stop.json`` if it is an in-root file, else ``None``."""
    return _probe(project_root / LAST_STOP_RELATIVE, project_root, "last-stop", want_dir=False)


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
    require_safe_ticket_id_segment(ticket_id)
    results_dir = find_results_dir(project_root)
    if results_dir is None:
        return None
    path = _probe(results_dir / f"{ticket_id}.json", project_root, "lane result", want_dir=False)
    if path is None:
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
    require_safe_ticket_id_segment(ticket_id)
    receipts_dir = find_receipts_dir(project_root)
    if receipts_dir is None:
        return False
    receipt = receipts_dir / f"{ticket_id}.json"
    return _probe(receipt, project_root, "receipt", want_dir=False) is not None


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

    That buys one INTERPRETATION of the format, not one read of the file: this
    call parses the file itself, so a caller that also resolves run-state parses
    it twice (see :meth:`~factory_console.services.run_service.RunService.list_records`,
    which documents the count). The duplicate read is cheap and the two parses
    cannot disagree about the format; they can, in principle, observe different
    revisions of a file the factory rewrites mid-request.

    Only the JSON form carries PR urls; a marker-directory source (or no source at
    all) has none, so the answer is empty.
    """
    if source is None or source.kind != "json":
        return {}
    return read_json_run_state(source.path).pr_urls
