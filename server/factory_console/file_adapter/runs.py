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

The reads are bounded, and bounded at the READ and not merely at a preceding
``stat`` — see :func:`_read_json_artifact`. A file over :data:`MAX_ARTIFACT_BYTES`
is not read, and the cap is REPORTED as ``too_large`` rather than short-read in
silence. Only a REGULAR file is read at all: a size is a bound on a regular file
and on nothing else.

Both of those gates are applied to an OPEN DESCRIPTOR rather than to a path, which
is what makes them hold. The artifacts are written by a process the console does not
control, so anything decided by name between two lookups describes a file that may
no longer be the one read — see :func:`_read_json_artifact` for what that let
through.

``read_result``/``read_receipt`` turn a ticket id into a filesystem path segment,
so they re-validate the id
(:func:`~factory_console.file_adapter.path_safety.validate_ticket_id_as_segment`)
and then check the RESOLVED path is still contained under the project root
(:func:`~factory_console.file_adapter.path_safety.within_root`). Only the ID half
raises the shared
:class:`~factory_console.file_adapter.path_safety.PathTraversal`: a containment
failure — undecidable OR a proven escape — answers ``unreadable``, exactly as
:func:`read_last_stop` answers it. A resolved path that leaves the root is a fact
about the TREE, not about an id these readers have already proven well-formed, and
``invalid_ticket_id`` is reserved for an id that is actually invalid.
:mod:`~factory_console.file_adapter.ticket_md` and
:mod:`~factory_console.file_adapter.write_render` impose containment too, and this
module's check is deliberately STRICTER than theirs on both halves — do not read the
three as interchangeable. Their id check is the pattern alone, while
``validate_ticket_id_as_segment`` also rejects bare ``.``/``..``; and their
containment resolves inline, so a resolution that itself fails escapes as a
``RuntimeError`` where this one answers ``unreadable``. Converging them is a follow-up,
which is why the shared rule now lives in
:mod:`~factory_console.file_adapter.path_safety` rather than here. Note also that
neither :mod:`~factory_console.file_adapter.run_state` nor
:mod:`~factory_console.file_adapter.ledger` resolves or contains anything, so this is
the first artifact reader under ``.factory/`` to impose the check rather than one
more module inheriting a house-wide habit.

:func:`read_last_stop` takes no ticket id, but it gets the CONTAINMENT half of that
check anyway (:func:`~factory_console.file_adapter.path_safety.within_root`, applied
inline). Owning the path a caller asks for
is not the same as owning the file it lands on: ``.factory/last-stop.json`` is a
constant, and a symlink there — or at ``.factory`` itself — still resolves wherever
it likes, which is precisely the escape the results/receipts gate exists to close
and which no amount of id validation was ever going to catch. Its containment
failure answers ``unreadable`` rather than raising — and so does the identical
failure in results/receipts, which is the point: the CONDITION decides the answer,
not which reader happened to observe it.

The console MUST NOT write, create, or delete anything here — a guard test
asserts this module contains no filesystem-mutating call.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

from factory_console.domain.runs import ArtifactRead
from factory_console.file_adapter.path_safety import (
    resolve_or_none,
    validate_ticket_id_as_segment,
    within_root,
)

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


def _artifact_candidate(project_root: Path, relative_dir: Path, ticket_id: str) -> Path:
    """The path ``<project_root>/<relative_dir>/<ticket_id>.json`` names, unresolved.

    The single owner of that join, so the path a refusal REPORTS and the path the
    gates were applied to cannot be derived differently — they were previously
    spelled out twice, which is a layout change away from disagreeing.
    """
    return project_root / relative_dir / f"{ticket_id}.json"


def _safe_artifact_path(project_root: Path, relative_dir: Path, ticket_id: str) -> Path | None:
    """Resolve ``<project_root>/<relative_dir>/<ticket_id>.json``, refusing an unsafe id.

    Two independent gates, in order and BOTH before any read: the id must be one
    path-safe segment
    (:func:`~factory_console.file_adapter.path_safety.validate_ticket_id_as_segment`),
    and the resolved candidate must stay under the resolved project root
    (:func:`~factory_console.file_adapter.path_safety.within_root`).

    Returns ``None`` for EITHER containment answer that is not a yes — the path (or
    the root) could not be resolved at all, or it resolved and provably escaped — and
    the caller reports ``unreadable`` for both. Only the ID gate raises. A proven
    escape is a fact about the resolved TREE (a symlinked ``.factory``), not about the
    id, which this function has just proven well-formed; answering it with
    ``invalid_ticket_id`` would accuse a value that is not at fault and send an
    operator to check an id they will find correct. It is the same condition
    :func:`read_last_stop` meets with no id at all, and one condition gets one answer
    across this module's readers.

    Raises:
        PathTraversal: when the id is not a single path-safe segment — the uniform
            ``invalid_ticket_id`` contract, and now its only cause here. Raised
            BEFORE any filesystem read.
    """
    validate_ticket_id_as_segment(ticket_id)
    resolved = resolve_or_none(_artifact_candidate(project_root, relative_dir, ticket_id))
    if resolved is None:
        return None
    # ``within_root`` is three-valued and both of its non-yes answers refuse here:
    # ``None`` (undecidable) and ``False`` (a proven escape) alike mean this console
    # will not read the path, and neither says anything about the ticket id.
    if not within_root(resolved, project_root):
        return None
    return resolved


def _read_ticket_artifact(project_root: Path, relative_dir: Path, ticket_id: str) -> ArtifactRead:
    """The shared body of :func:`read_result` and :func:`read_receipt`.

    Gate the path, then read it. Factored out so the two readers cannot drift in
    how they handle an unresolvable path — a receipt and a result are two artifacts
    of the same kind, and this module must not tolerate a malformed one differently
    from the other.
    """
    safe = _safe_artifact_path(project_root, relative_dir, ticket_id)
    if safe is None:
        candidate = _artifact_candidate(project_root, relative_dir, ticket_id)
        # The path did not resolve, the ROOT did not resolve, or it resolved outside
        # the root — three ways of not landing on a readable in-project file, and the
        # message names all of them rather than only the artifact: pointing an operator
        # at ``.factory/results/<id>.json`` when the problem is the project root or a
        # symlinked ``.factory`` sends them to inspect a file that is not at fault. It
        # is the id-carrying twin of the line :func:`read_last_stop` logs, because it
        # is the same condition.
        _LOGGER.warning(
            "runs: %s does not resolve to a path inside the project %s; it is not read",
            candidate,
            project_root,
        )
        return ArtifactRead(path=_reportable_path(candidate), reason="unreadable")
    return _read_json_artifact(safe)


def _reportable_path(candidate: Path) -> Path:
    """The path an :class:`ArtifactRead` carries when the read never got started.

    Every outcome that reaches :func:`_read_json_artifact` reports the RESOLVED path,
    so the refusal branches must too or ``ArtifactRead.path`` means one thing on most
    answers and another on two of them — and a caller keying artifacts by it gets two
    keys for one file whenever the project root is relative or symlinked.

    BOTH refusal conditions arrive here, and they are not the same, so neither may be
    assumed. The path (or the root) could not be resolved AT ALL — in which case the
    retry below fails again and falls back to :meth:`Path.absolute`, which performs no
    filesystem lookup and so cannot fail the same way, rather than to the bare join.
    Or the path resolved and provably LEFT the root, in which case the retry succeeds
    and this reports the escape target — deliberately, per Amendment 1: a refusal names
    the path that could not be used, and the out-of-root target is that path.
    ``tests/unit/test_runs.py`` pins it. Do not "simplify" this to an unconditional
    :meth:`Path.absolute` on the reading that resolution has already failed — it has
    not, on the second branch, and that would silently change what an escape reports.
    """
    resolved = resolve_or_none(candidate)
    if resolved is not None:
        return resolved
    try:
        return candidate.absolute()
    except (OSError, ValueError):
        return candidate


def _read_json_artifact(path: Path) -> ArtifactRead:
    """Read ONE JSON artifact at ``path`` into an :class:`ArtifactRead`.

    The shared body of all three public readers, so absence, unreadability, the
    size cap and every parse failure are decided in ONE place and cannot drift
    between results, receipts and last-stop.

    NEVER raises. Every failure becomes a named reason (see
    :data:`~factory_console.domain.runs.ArtifactSkipReason`): the file is OPENED
    first, then every gate — node type, size, byte bound — is applied to the opened
    descriptor, then parsed, and only a top-level JSON OBJECT counts as data.

    The open comes first, and that ordering is the point. A ``stat`` followed by an
    ``open`` of the same NAME are two independent lookups, and this module's own
    threat model — ``.factory/`` is written by a process the console does not control
    and may belong to an untrusted checkout — is exactly the one where a name can be
    re-pointed between them. Deciding from the name meant the containment,
    regular-file and size verdicts all described a file that need no longer be the one
    read: swapping in a symlink after the check returned any JSON the server process
    could open as this project's artifact with ``reason is None`` on it, and swapping
    in a FIFO reinstated the blocking ``open`` the ``S_ISREG`` gate was written to
    prevent. Opening once and interrogating the DESCRIPTOR closes both: there is only
    one lookup left to race, and ``os.fstat`` cannot describe a different file from
    the one the bytes come from.

    KNOWN RESIDUAL, stated so it is not mistaken for closed: the containment check in
    :func:`_safe_artifact_path`/:func:`read_last_stop` still runs against a NAME, and
    ``O_NOFOLLOW`` only refuses a symlink as the FINAL component. An INTERMEDIATE
    component swapped between the check and this open — ``.factory`` or
    ``.factory/results`` replaced by a symlink out of the root — is still followed by
    the kernel, so a sufficiently well-timed local writer can still be read through.
    Closing it needs a component-by-component ``os.open(..., O_DIRECTORY | O_NOFOLLOW,
    dir_fd=...)`` descent from the project root, and that is NOT a drop-in hardening:
    this module deliberately RESOLVES symlinks and then bounds the result, which
    permits the legitimate deployment where ``.factory`` is a symlink to a shared
    artifacts mount or another worktree that still lands inside the root. A nofollow
    descent refuses that case too, so choosing it is a contract decision about which
    project layouts the console supports, not a bug fix — see T89/T90 before making it.
    """
    try:
        # ``O_NOFOLLOW`` refuses a symlink swapped in as the final component — the path
        # was already fully resolved by the caller, so a symlink here is by definition
        # one that appeared after the containment check and has no legitimate reading.
        # ``O_NONBLOCK`` makes the open itself total: on a FIFO it returns immediately
        # instead of blocking forever waiting for a writer, so the node-type check below
        # gets to run and reject it. Both are absent on some platforms; ``getattr``
        # degrades to 0 rather than an AttributeError, which is the same
        # interpreter/platform-drift care ``_resolve_or_none`` takes.
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(path, flags)
    except (FileNotFoundError, NotADirectoryError):
        # Nothing is there — no file, or a path component that is not a directory,
        # which amounts to the same thing. The ordinary state of a fresh clone, so
        # it is not logged: a project the factory has not run on is not a
        # degradation.
        return ArtifactRead(path=path, reason="absent")
    except OSError as error:
        # It may well be there and we could not look: EACCES, EIO, EISDIR (a directory
        # cannot be opened O_RDONLY), and ELOOP for the swapped-in symlink O_NOFOLLOW
        # just refused. ``%r`` on the cause, per this package's convention: an
        # OSError's text carries the offending filename, the log formatter is one
        # record per line, and an unescaped newline in it would forge a record.
        _LOGGER.warning("runs: %s could not be opened: %r", path, error)
        return ArtifactRead(path=path, reason="unreadable")
    except ValueError:
        # A path that cannot be encoded (an embedded NUL). ``os.open`` raises this
        # rather than an ``OSError``, so the clause above does not cover it, and the
        # NEVER-raises contract is stated without exceptions — the same
        # ``except ValueError`` the run-state probes carry.
        _LOGGER.warning("runs: an artifact path could not be encoded; it is not read")
        return ArtifactRead(path=path, reason="unreadable")

    try:
        try:
            info = os.fstat(descriptor)
        except OSError as error:
            _LOGGER.warning("runs: %s could not be stat'd: %r", path, error)
            return ArtifactRead(path=path, reason="unreadable")

        if not stat.S_ISREG(info.st_mode):
            # Only a REGULAR file is read, and the question is now asked of the OPENED
            # file, so the answer cannot be invalidated by a later swap. A size is a
            # bound on a regular file and on nothing else: a FIFO and a character
            # device both stat as ``st_size == 0``, so each would sail past the cap
            # below — and ``/dev/zero`` reached this way never sees EOF and would be
            # read until the process dies. The same rule ``ledger.find_ledger_path``
            # states with ``is_file()`` and ``run_state.find_run_state_source`` with
            # ``_is_regular_file``.
            #
            # This also settles the DIRECTORY case, and it is the ONLY thing that does,
            # on every platform this project supports. ``O_RDONLY`` does NOT fail on a
            # directory: EISDIR is raised for ``O_WRONLY``/``O_RDWR`` only, so the open
            # above succeeds on Linux and macOS and this check is the load-bearing
            # refusal, not a backstop for some other gate — do not remove it as
            # redundant. Before it, a directory reached ``unreadable`` only because
            # ``read_bytes`` raised ``IsADirectoryError``, which meant a directory whose
            # ``st_size`` happened to exceed the cap (large directories on ext4/XFS) was
            # reported ``too_large`` instead — two of the four reasons this module
            # promises never to conflate.
            _LOGGER.warning("runs: %s is not a regular file; it is not read", path)
            return ArtifactRead(path=path, reason="unreadable")

        if info.st_size > MAX_ARTIFACT_BYTES:
            _LOGGER.warning(
                "runs: %s is %d bytes, over the %d-byte cap; not read",
                path,
                info.st_size,
                MAX_ARTIFACT_BYTES,
            )
            return ArtifactRead(path=path, reason="too_large")

        try:
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                # Bounded at the READ, not merely at the fstat above. That size is
                # already stale by the time it is acted on — this module's own comments
                # note the factory rewrites these files while the console is running —
                # so a file that stats at 18 bytes and is extended to gigabytes before
                # this line would otherwise be pulled into memory whole, which is the
                # unbounded read on a request path the cap exists to prevent. Reading
                # ``MAX + 1`` makes the cap a property of this call: at most one byte
                # over is ever held, and that byte is what distinguishes "exactly at
                # the cap" (read) from "over it" (reported).
                raw = handle.read(MAX_ARTIFACT_BYTES + 1)
        except OSError as error:
            # An I/O error on a descriptor that is already open. Note ``absent`` is NOT
            # reachable here any more and must not be restored: the file is open, so it
            # cannot vanish mid-read — unlinking it only drops the name, and this
            # descriptor keeps reading the inode. "It disappeared between the check and
            # the read" was an artefact of checking by name, and holding the descriptor
            # is what removed it.
            _LOGGER.warning("runs: %s could not be read: %r", path, error)
            return ArtifactRead(path=path, reason="unreadable")
    finally:
        # ``closefd=False`` above so this one owner closes the descriptor on every
        # path, including the early returns that never reach ``fdopen`` at all.
        #
        # Guarded, because ``close(2)`` is not infallible: it reports deferred errors
        # (EIO on NFS and some FUSE mounts) and can fail EBADF/EINTR under a descriptor
        # race. Raising from a ``finally`` REPLACES the ``ArtifactRead`` the branches
        # above already computed, so an unguarded close is the one hole in this
        # function's NEVER-raises contract — and the escaping ``OSError`` is not a
        # :class:`~factory_console.errors.FactoryConsoleError`, so the edge layer has no
        # handler for it and it surfaces as an unmapped 500. Swallowing is safe here and
        # nowhere else: the bytes are already read and the verdict already decided, so a
        # failed close cannot change the answer, only lose the descriptor.
        try:
            os.close(descriptor)
        except OSError as error:
            _LOGGER.warning("runs: %s could not be closed: %r", path, error)

    if len(raw) > MAX_ARTIFACT_BYTES:
        # The file GREW past the cap between the fstat and the read. Reported, never
        # parsed from the truncated prefix: a short read of a rewritten artifact is
        # a smaller, wrong record, and ``too_large`` is the same answer the
        # pre-read check gives for the same file one moment earlier.
        _LOGGER.warning(
            "runs: %s grew past the %d-byte cap while being read; not read",
            path,
            MAX_ARTIFACT_BYTES,
        )
        return ArtifactRead(path=path, reason="too_large")

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
        PathTraversal: if and ONLY if ``ticket_id`` is not a single path-safe
            segment — a pattern violation or a bare ``.``/``..``. Raised BEFORE any
            filesystem read — the same contract
            :mod:`~factory_console.file_adapter.run_state` carries. A containment
            failure is NOT this case, however it arises: whether the path could not
            be resolved (a symlink loop) or resolved provably outside
            ``project_root`` (a symlinked ``.factory``), the id is well-formed and
            the answer is ``unreadable``, as it is for :func:`read_last_stop`.
    """
    return _read_ticket_artifact(project_root, RESULTS_RELATIVE_DIR, ticket_id)


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
            read.
    """
    return _read_ticket_artifact(project_root, RECEIPTS_RELATIVE_DIR, ticket_id)


def read_last_stop(project_root: Path) -> ArtifactRead:
    """Read ``<project_root>/.factory/last-stop.json``.

    Why the LAST run stopped — one artifact per project, naming no ticket, so this
    takes no ticket id and has no id to validate.

    It is NOT therefore exempt from the CONTAINMENT check. Console-owned constants
    fix the path this asks for, not the file that path lands on: ``.factory/`` is
    written by a process the console does not control and may be a checkout of an
    untrusted repository, so a symlink at ``last-stop.json`` — or at ``.factory``
    itself — resolves wherever it points, and reading through it would return any
    JSON object the server process can open (a credentials file, another project's
    artifacts) as this project's last-stop record, with ``reason is None`` marking it
    a clean read. That is the same escape :func:`_safe_artifact_path` closes for
    results and receipts, and an id was never what made it possible. An escaping or
    unresolvable path answers ``unreadable`` here — it is there and this console will
    not look — and it answers ``unreadable`` there too, for the same reason: the
    condition is identical, so the report is identical.

    NEVER raises, for any input.
    """
    candidate = project_root / LAST_STOP_RELATIVE_PATH
    resolved = resolve_or_none(candidate)
    # Both of ``within_root``'s non-yes answers refuse, and refuse the same way —
    # ``None`` (undecidable) and ``False`` (a proven escape) alike mean this path is
    # not read. :func:`_safe_artifact_path` folds them identically.
    if resolved is None or not within_root(resolved, project_root):
        _LOGGER.warning(
            "runs: %s does not resolve to a path inside the project; it is not read", candidate
        )
        return ArtifactRead(path=_reportable_path(candidate), reason="unreadable")
    return _read_json_artifact(resolved)
