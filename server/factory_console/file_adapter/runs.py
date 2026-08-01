# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.
"""Read the factory's per-ticket run artifacts that sit beside ``run-state.json``.

The App Factory leaves four things under ``<project>/.factory/``. T78 owns the
PARSING of the first; this module reads the other three outright and takes the
``pr_url`` half of the first through T78's parser:

1. ``run-state.json`` — state + ``pr_url`` per ticket (T78's
   :mod:`~factory_console.file_adapter.run_state`; :func:`read_pr_urls` here
   calls that ONE parser rather than reading the format itself — see its
   docstring for what that does and does not buy). Its LOCATION is bounded here
   like every other artifact's, by :func:`find_run_state_path`.
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
from factory_console.file_adapter.path_safety import is_contained, require_safe_ticket_id_segment
from factory_console.file_adapter.run_state import read_json_run_state

_LOGGER = logging.getLogger(__name__)

RESULTS_RELATIVE = Path(".factory") / "results"
"""Project-relative location of the per-ticket lane results directory."""

RECEIPTS_RELATIVE = Path(".factory") / "receipts"
"""Project-relative location of the per-ticket review receipts directory."""

LAST_STOP_RELATIVE = Path(".factory") / "last-stop.json"
"""Project-relative location of the "why the last run stopped" file."""


def _probe(
    candidate: Path,
    project_root: Path,
    artifact: str,
    *,
    want_dir: bool,
    resolved_root: Path | None = None,
) -> Path | None:
    """Return ``candidate`` if it is an in-root directory/file, else ``None``.

    Containment is :func:`~factory_console.file_adapter.path_safety.is_contained`'s
    rule, not a copy of it: this function adds the node-type check, the "absent"
    degradation and the log around that one shared primitive. ``resolved_root`` is
    that primitive's optimisation-only pre-resolved root, passed straight through
    — the batched readers supply it so a per-ticket probe does not re-resolve an
    invariant root once per ticket.

    Out-of-root resolution is reported as ABSENT rather than raised: this module
    never raises for an artifact problem (only for an unsafe ticket id), and
    "there is no run data here" is the honest answer for an artifact the console
    is not allowed to read. It is logged so the degradation is attributable.
    """
    if not (candidate.is_dir() if want_dir else candidate.is_file()):
        return None
    if not is_contained(candidate, project_root, resolved_root=resolved_root):
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


def read_result_in(
    results_dir: Path | None,
    project_root: Path,
    ticket_id: str,
    *,
    resolved_root: Path | None = None,
) -> RunResultSummary | None:
    """Return the lane result for ``ticket_id`` from an ALREADY-RESOLVED results dir.

    The directory-taking half of :func:`read_result`, split out so a caller
    composing many tickets resolves ``.factory/results`` ONCE per request instead
    of once per ticket (each resolution is a stat plus two ``Path.resolve()``
    calls, so the per-ticket form costs O(N) redundant syscalls on the list path).
    ``None`` for ``results_dir`` means the directory itself was absent or
    out-of-root, which is just another "no answer".

    ``resolved_root`` is the same optimisation one level down: the containment
    check still resolves the CANDIDATE per ticket (it must — that is the whole
    check), but the ROOT it is compared against is invariant, so a batched caller
    passes it pre-resolved rather than making every ticket re-walk it.

    Raises:
        PathTraversal: if ``ticket_id`` is not a single path-safe segment, raised
            BEFORE the results path is joined or probed.
    """
    require_safe_ticket_id_segment(ticket_id)
    if results_dir is None:
        return None
    path = _probe(
        results_dir / f"{ticket_id}.json",
        project_root,
        "lane result",
        want_dir=False,
        resolved_root=resolved_root,
    )
    if path is None:
        return None
    document = _load_json_object(path, "lane result")
    if document is None:
        return None
    try:
        summary = RunResultSummary.model_validate(document)
    except ValidationError:
        # A modelled key present with the WRONG type (``review_iterations:
        # "two"``). Rare, but the factory owns this file, so a type change there
        # must not 500 the runs endpoint — degrade to "no result", which the
        # caller reports by naming ``results`` in ``unavailable``.
        _LOGGER.warning(
            "lane result: %s does not match the expected shape; treating it as absent", path
        )
        return None
    if not summary.model_fields_set:
        # A document that set NO modelled field. ``extra="ignore"`` plus five
        # optional fields means a lane result whose keys simply differ from the
        # assumed ``===LANE_RESULT===`` names validates CLEANLY into an all-null
        # summary — and a non-``None`` summary is one the caller reports as an
        # answered source, so the response becomes exactly the "every field null,
        # nothing named in ``unavailable``" shape this endpoint exists to prevent.
        # A renamed key never raises ``ValidationError`` (only a wrong TYPE on a
        # modelled key does), so the branch above cannot catch it. The schema is
        # unverified against a real file (see :class:`RunResultSummary`), which
        # makes this the likeliest form of disagreement, not a hypothetical one.
        _LOGGER.warning(
            "lane result: %s carries none of the modelled fields; treating it as absent", path
        )
        return None
    return summary


def read_result(project_root: Path, ticket_id: str) -> RunResultSummary | None:
    """Return the lane result for ``ticket_id``, or ``None`` when there is none.

    ``None`` covers every "no answer" case — no ``results`` directory, no file for
    this ticket, an unreadable or non-object file, or a file that names none of
    the modelled fields — because the caller reports the reason by NAMING the
    source in ``RunRecord.unavailable`` rather than by distinguishing null from
    null.

    Resolves the results directory itself; a caller reading many tickets should
    resolve it once and use :func:`read_result_in` instead.

    Raises:
        PathTraversal: if ``ticket_id`` is not a single path-safe segment, raised
            BEFORE the results path is joined or probed.
    """
    require_safe_ticket_id_segment(ticket_id)
    return read_result_in(find_results_dir(project_root), project_root, ticket_id)


def has_receipt_in(
    receipts_dir: Path | None,
    project_root: Path,
    ticket_id: str,
    *,
    resolved_root: Path | None = None,
) -> bool:
    """True if ``<receipts_dir>/<ticket_id>.json`` exists, for an ALREADY-RESOLVED dir.

    The directory-taking half of :func:`has_receipt`, split out for the same
    per-request-not-per-ticket reason as :func:`read_result_in`, and taking the
    same optimisation-only pre-resolved ``resolved_root``.

    Raises:
        PathTraversal: if ``ticket_id`` is not a single path-safe segment, raised
            BEFORE the receipts path is joined or probed.
    """
    require_safe_ticket_id_segment(ticket_id)
    if receipts_dir is None:
        return False
    receipt = receipts_dir / f"{ticket_id}.json"
    return (
        _probe(receipt, project_root, "receipt", want_dir=False, resolved_root=resolved_root)
        is not None
    )


def has_receipt(project_root: Path, ticket_id: str) -> bool:
    """True if ``.factory/receipts/<ticket_id>.json`` exists as a file.

    PRESENCE ONLY — receipt content is not parsed or modelled anywhere in this
    console (see :class:`~factory_console.domain.run_record.RunRecord`).

    Raises:
        PathTraversal: if ``ticket_id`` is not a single path-safe segment, raised
            BEFORE the receipts path is joined or probed.
    """
    require_safe_ticket_id_segment(ticket_id)
    return has_receipt_in(find_receipts_dir(project_root), project_root, ticket_id)


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


def find_run_state_path(source: RunStateSource | None, project_root: Path) -> Path | None:
    """Return the run-state source's path if it is in-root, else ``None``.

    Run-state is the fourth artifact this module surfaces, and it goes through the
    same :func:`_probe` as the other three so the module's stated invariant holds
    for ALL of them: a symlinked ``.factory/run-state.json`` pointing outside the
    project root is neither read nor reported as found, rather than being read
    while the endpoint renders its LEXICAL, still-in-root-looking path.

    DEFENSE-IN-DEPTH, not the primary guard.
    :func:`~factory_console.file_adapter.run_state.find_run_state_source` now
    applies the same containment rule when it RESOLVES the source, so an escaping
    artifact never reaches a ``Project`` and this function is normally handed
    ``None``. That is deliberately where the primary check lives: containment
    enforced only here would bound what this module reads while ``list_tickets``
    and ``read_run_state`` — which take the source straight off the ``Project`` —
    went on parsing the same out-of-root file, so the endpoint would report
    ``found: false`` beside ticket states read out of it. The check is kept here
    anyway because this function accepts a caller-supplied source and must not
    depend on its provenance.
    """
    if source is None:
        return None
    return _probe(source.path, project_root, "run-state", want_dir=source.kind == "directory")


def read_pr_urls(source: RunStateSource | None, project_root: Path) -> dict[str, str]:
    """Return ``{ticket_id: pr_url}`` from the project's run-state source.

    Delegates to T78's :func:`~factory_console.file_adapter.run_state.read_json_run_state`
    so ``run-state.json`` keeps exactly ONE parser — a second reader here would be
    a second authority on the factory's format, free to drift from the first.

    That buys one INTERPRETATION of the format, not one read of the file: this
    call parses the file itself, so a caller that also resolves run-state parses
    it at least twice (see
    :meth:`~factory_console.services.run_service.RunService.list_records` and
    :meth:`~factory_console.services.run_service.RunService.get_record`, which
    each document their own count). The duplicate read is cheap and the parses
    cannot disagree about the format; they can, in principle, observe different
    revisions of a file the factory rewrites mid-request.

    Only the JSON form carries PR urls; a marker-directory source, no source at
    all, or a source that resolves out-of-root has none, so the answer is empty.
    That form test is
    :attr:`~factory_console.domain.run_state_source.RunStateSource.carriesPrUrls`,
    the same one
    :meth:`~factory_console.services.run_service.RunService._carries_pr_urls`
    reads, so this cannot return no urls while the service reports the source as
    one that answered.
    """
    if source is None or not source.carriesPrUrls:
        return {}
    path = find_run_state_path(source, project_root)
    if path is None:
        return {}
    return read_json_run_state(path).pr_urls
