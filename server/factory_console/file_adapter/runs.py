# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.
"""Read the factory's per-run JSON artifacts beside ``.factory/run-state.json``.

Three artifacts, all written by the App Factory while the console is running, all
optional, all readable through one shared code path:

- ``.factory/results/<ticket_id>.json`` — one lane's result summary
  (:func:`read_result`).
- ``.factory/receipts/<ticket_id>.json`` — that lane's review receipt
  (:func:`read_receipt`).
- ``.factory/last-stop.json`` — why the last run stopped (:func:`read_last_stop`).
  It carries no ticket id, so it takes none.

Every read answers with an :class:`~factory_console.domain.runs.ArtifactRead`,
never a bare ``None``: ``.factory/`` is gitignored, so a fresh clone has none of
these, and a file that is missing must stay tellable apart from one that is there
and could not be read. See :data:`~factory_console.domain.runs.ArtifactSkipReason`
for the four reasons and what separates them.

The reads are bounded: a file over :data:`MAX_ARTIFACT_BYTES` is not read at all,
and the cap is REPORTED as ``too_large`` rather than short-read in silence.

``read_result``/``read_receipt`` turn a ticket id into a filesystem path segment,
so they re-validate the id (:func:`_validate_ticket_id_as_segment`) and then check
the RESOLVED path is still contained under the project root — the same
defense-in-depth :mod:`~factory_console.file_adapter.run_state` and
:mod:`~factory_console.file_adapter.ticket_md` apply, raising the same shared
:class:`~factory_console.file_adapter.path_safety.PathTraversal`.

The console MUST NOT write, create, or delete anything here — a guard test
asserts this module contains no filesystem-mutating call.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from factory_console.domain.runs import ArtifactRead
from factory_console.domain.ticket import TICKET_ID_PATTERN
from factory_console.file_adapter.path_safety import PathTraversal

_LOGGER = logging.getLogger(__name__)

# The artifacts' project-relative locations. Single source of truth for WHERE
# each lives; the public readers probe exactly these under a project root.
RESULTS_RELATIVE_DIR = Path(".factory") / "results"
RECEIPTS_RELATIVE_DIR = Path(".factory") / "receipts"
LAST_STOP_RELATIVE_PATH = Path(".factory") / "last-stop.json"

# Hard cap on the bytes this reader will pull into memory for ONE artifact. Each
# of these files is written by a process the console does not control, so "read
# the whole file" is otherwise an unbounded read on a request path. The cap is
# far smaller than the ledger's :data:`~factory_console.file_adapter.ledger.MAX_LEDGER_BYTES`
# because these are not the same kind of file: the ledger is APPENDED to forever,
# so it grows with a project's whole history, while each of these is a single
# small JSON object rewritten in place — a lane summary or a receipt, kilobytes
# at most. 1 MiB is therefore already three orders of magnitude of headroom, and
# a file past it means something is wrong with the file, not that a lane got
# busy. Exceeding it is REPORTED (``too_large``), never silently truncated into a
# smaller, wrong record.
MAX_ARTIFACT_BYTES = 1 * 1024 * 1024


def _validate_ticket_id_as_segment(ticket_id: str) -> None:
    """Raise :class:`PathTraversal` unless ``ticket_id`` is one path-safe segment.

    Defense-in-depth: the id was already validated at the API boundary, but this
    module joins it onto a filesystem path, so it is re-validated at the point of
    use. ``fullmatch`` (not ``match``) so a trailing newline cannot sneak past the
    ``$`` anchor. :data:`TICKET_ID_PATTERN` allows ``.`` as a character, so bare
    ``.`` and ``..`` pass the regex yet are single-segment traversals — they are
    rejected explicitly, exactly as
    :mod:`~factory_console.file_adapter.run_state` rejects them.
    """
    if re.fullmatch(TICKET_ID_PATTERN, ticket_id) is None or ticket_id in (".", ".."):
        raise PathTraversal(ticket_id)


def _safe_artifact_path(project_root: Path, relative_dir: Path, ticket_id: str) -> Path:
    """Resolve ``<project_root>/<relative_dir>/<ticket_id>.json``, refusing an unsafe id.

    Two independent gates, in order and BOTH before any read: the id must be one
    path-safe segment (:func:`_validate_ticket_id_as_segment`), and the RESOLVED
    candidate must stay under the resolved project root — so a symlinked
    ``.factory/results`` pointing out of the project cannot be read through, which
    no amount of id validation can catch. Both sides are resolved so a symlinked
    root (``/tmp`` on some platforms) is not a false escape, mirroring
    :func:`~factory_console.file_adapter.ticket_md._safe_resolve`.

    Raises:
        PathTraversal: for either cause, with the uniform ``invalid_ticket_id``
            contract. Raised BEFORE any filesystem read.
    """
    _validate_ticket_id_as_segment(ticket_id)
    candidate = (project_root / relative_dir / f"{ticket_id}.json").resolve(strict=False)
    if not candidate.is_relative_to(project_root.resolve()):
        raise PathTraversal(ticket_id, reason="Ticket id resolves outside the project root")
    return candidate


def _read_json_artifact(path: Path) -> ArtifactRead:
    """Read ONE JSON artifact at ``path`` into an :class:`ArtifactRead`.

    The shared body of all three public readers, so absence, unreadability, the
    size cap and every parse failure are decided in ONE place and cannot drift
    between results, receipts and last-stop.

    NEVER raises. Every failure becomes a named reason (see
    :data:`~factory_console.domain.runs.ArtifactSkipReason`): the file is stat'd
    first so an oversized one is refused before its bytes are touched, then read,
    then parsed, and only a top-level JSON OBJECT counts as data.
    """
    try:
        size = path.stat().st_size
    except (FileNotFoundError, NotADirectoryError):
        # Nothing is there — no file, or a path component that is not a directory,
        # which amounts to the same thing. The ordinary state of a fresh clone, so
        # it is not logged: a project the factory has not run on is not a
        # degradation.
        return ArtifactRead(path=path, reason="absent")
    except OSError as error:
        # It may well be there and we could not look (EACCES, EIO). ``%r`` on the
        # cause, per this package's convention: an OSError's text carries the
        # offending filename, the log formatter is one record per line, and an
        # unescaped newline in it would forge a record.
        _LOGGER.warning("runs: %s could not be stat'd: %r", path, error)
        return ArtifactRead(path=path, reason="unreadable")

    if size > MAX_ARTIFACT_BYTES:
        _LOGGER.warning(
            "runs: %s is %d bytes, over the %d-byte cap; not read",
            path,
            size,
            MAX_ARTIFACT_BYTES,
        )
        return ArtifactRead(path=path, reason="too_large")

    try:
        raw = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        # It vanished between the stat and the read — the factory rewrites these
        # files while the console is running. Still "there is nothing to find",
        # not "I could not look", so it keeps the ``absent`` reason.
        return ArtifactRead(path=path, reason="absent")
    except OSError as error:
        # Everything else: PermissionError, an I/O error, and IsADirectoryError
        # for a directory sitting where the artifact belongs (which stats fine
        # above and only fails here).
        _LOGGER.warning("runs: %s could not be read: %r", path, error)
        return ArtifactRead(path=path, reason="unreadable")

    # Decode with replacement rather than strictly, for the same reason the ledger
    # does: a byte-level corruption must not cost more than this one file. Note the
    # difference from the ledger, though — there, replacement confines the damage to
    # the one LINE that will fail to parse, while here there is no finer unit than
    # the file, so a single corrupt byte fails the WHOLE artifact as ``unparseable``.
    # That is the honest outcome either way: replacement changes nothing about what
    # is reported, it only keeps a UnicodeDecodeError from escaping as a crash.
    text = raw.decode("utf-8", errors="replace")
    try:
        document = json.loads(text)
    except (ValueError, RecursionError, MemoryError):
        # Not just ``JSONDecodeError`` (a ``ValueError`` subclass): this artifact is
        # written by another process, and ``json.loads`` answers pathological input
        # with exceptions outside that type — deeply nested arrays raise
        # ``RecursionError``, a huge document ``MemoryError``. Letting either escape
        # would break the NEVER-raises contract and 500 a request until the file
        # changed.
        _LOGGER.warning("runs: %s is not valid JSON", path)
        return ArtifactRead(path=path, reason="unparseable")

    if not isinstance(document, dict):
        # Valid JSON of the wrong SHAPE — a list, a bare string, ``null``. It is
        # ``unparseable`` and not a successful empty read: the artifact contract is
        # one JSON OBJECT, and handing a caller ``data=None`` with no reason would be
        # the very absent/malformed collapse this result type exists to prevent.
        _LOGGER.warning("runs: %s is not a JSON object (found %s)", path, type(document).__name__)
        return ArtifactRead(path=path, reason="unparseable")

    return ArtifactRead(path=path, data=document)


def read_result(project_root: Path, ticket_id: str) -> ArtifactRead:
    """Read ``<project_root>/.factory/results/<ticket_id>.json``.

    The lane result summary the factory writes for one ticket. Returns the parsed
    object, or a named reason why not — a project the factory has never run on
    yields ``absent`` for every id, which is an ordinary answer and not an error.

    NEVER raises for any filesystem or content problem; see
    :func:`_read_json_artifact`.

    Raises:
        PathTraversal: if ``ticket_id`` is not a single path-safe segment, or if
            it resolves outside ``project_root``. Raised BEFORE any filesystem
            access — the same contract
            :mod:`~factory_console.file_adapter.run_state` carries.
    """
    return _read_json_artifact(_safe_artifact_path(project_root, RESULTS_RELATIVE_DIR, ticket_id))


def read_receipt(project_root: Path, ticket_id: str) -> ArtifactRead:
    """Read ``<project_root>/.factory/receipts/<ticket_id>.json``.

    The review receipt for one ticket. Identical in every respect to
    :func:`read_result` but for the directory it reads, deliberately: a receipt
    and a result are two artifacts of the same kind, and the console must not
    tolerate a malformed one differently from the other.

    NEVER raises for any filesystem or content problem; see
    :func:`_read_json_artifact`.

    Raises:
        PathTraversal: exactly as :func:`read_result`, before any filesystem
            access.
    """
    return _read_json_artifact(_safe_artifact_path(project_root, RECEIPTS_RELATIVE_DIR, ticket_id))


def read_last_stop(project_root: Path) -> ArtifactRead:
    """Read ``<project_root>/.factory/last-stop.json``.

    Why the LAST run stopped — one artifact per project, naming no ticket, so this
    takes no ticket id and has nothing to path-validate: the whole path is
    console-owned constants under a root the caller already resolved.

    NEVER raises, for any input: with no id to reject there is no
    :class:`PathTraversal` path here either.
    """
    return _read_json_artifact(project_root / LAST_STOP_RELATIVE_PATH)
