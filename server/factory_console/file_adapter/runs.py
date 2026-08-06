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
:mod:`~factory_console.file_adapter.path_safety` rather than here. This was the FIRST
artifact reader under ``.factory/`` to impose the check rather than one more module
inheriting a house-wide habit; :mod:`~factory_console.file_adapter.ledger` has since
adopted the containment half in its own ``find_ledger_path``, phrasing the refusal as
its "I could not look" raise instead of as ``unreadable``.
:mod:`~factory_console.file_adapter.run_state` still resolves and contains nothing.

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
from pathlib import Path

from factory_console.domain.runs import ArtifactRead
from factory_console.file_adapter.bounded_read import read_bounded
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


def artifact_candidate(project_root: Path, relative_dir: Path, ticket_id: str) -> Path:
    """The path ``<project_root>/<relative_dir>/<ticket_id>.json`` names, unresolved.

    The single owner of that join, so the path a refusal REPORTS and the path the
    gates were applied to cannot be derived differently — they were previously
    spelled out twice, which is a layout change away from disagreeing.

    PUBLIC, unlike the rest of this module's path helpers, for the one caller that
    needs the join WITHOUT the resolution :func:`refusal_path` applies:
    :class:`~factory_console.file_adapter.run_artifacts.FakeRunArtifactReader`
    reports unresolved paths on purpose (resolving would be I/O, and performing
    none is that class's whole reason to exist). It re-spelled this join inline
    until it was pointed at here — which is precisely the "layout change away from
    disagreeing" this function exists to prevent, one seam further out: a change to
    the artifact filename convention would otherwise move the real reader and leave
    the fake, and every fake-backed test, asserting the old shape.
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
    resolved = resolve_or_none(artifact_candidate(project_root, relative_dir, ticket_id))
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
        candidate = artifact_candidate(project_root, relative_dir, ticket_id)
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


def refusal_path(project_root: Path, relative_dir: Path, ticket_id: str) -> Path:
    """The path an :class:`ArtifactRead` reports when this module REFUSED an id.

    The resolved, root-clamped spelling of :func:`artifact_candidate` composed with
    :func:`_reportable_path`, for the one refusal this module cannot make itself:
    :func:`validate_ticket_id_as_segment` RAISES on a path-unsafe id, so a caller
    looping over a whole manifest catches
    :class:`~factory_console.file_adapter.path_safety.PathTraversal` and reports
    ``unreadable`` on its own — see
    :class:`~factory_console.file_adapter.run_artifacts.RealRunArtifactReader`.

    It exists so that caller does not re-derive the join. :func:`artifact_candidate`'s
    docstring already states why the join has exactly one owner ("they were
    previously spelled out twice, which is a layout change away from disagreeing"),
    and ``_reportable_path``'s states why a refusal reports a RESOLVED path
    ("a caller keying artifacts by it gets two keys for one file whenever the
    project root is relative or symlinked"). Both invariants are module-wide, not
    function-wide, so the refusal made one layer up must go through them rather
    than around them.

    CLAMPED to the project, and that gate is load-bearing rather than belt-and-braces.
    This is the one path helper reached AFTER
    :func:`~factory_console.file_adapter.path_safety.validate_ticket_id_as_segment`
    has REFUSED the id, so it is the one that must not join that id and hand back
    wherever it lands. Both of the validator's rules arrive here, and only the bare
    ``.``/``..`` one is harmless: the ``.json`` suffix turns those into the ordinary
    in-root names ``..json``/``...json``. A TICKET_ID_PATTERN violation is the live
    case — the port
    (:class:`~factory_console.file_adapter.run_artifacts.RunArtifactReader`) types its
    id ``str`` and promises totality for a "path-unsafe" one, so a caller handing it
    ``../../../../etc/passwd`` would otherwise get ``/etc/passwd.json`` — resolved,
    symlinks followed — inside :attr:`~factory_console.domain.runs.ArtifactRead.path`,
    a field built to be SHOWN. That is exactly what
    :class:`~factory_console.file_adapter.path_safety.PathTraversal` forbids in its own
    docstring: an unsafe id is answered with the id, "never a resolved absolute path,
    which would disclose the server's filesystem layout".

    So an id that cannot form an in-root filename is answered with the artifact
    DIRECTORY. Note the containment test must fail CLOSED: ``within_root`` is
    three-valued, and ``None`` ("could not be decided", an unresolvable root) clamps
    here exactly like a proven escape. Reporting the directory also keeps the
    out-of-root spelling meaning ONE thing — ``_reportable_path``'s case 2, a PROVEN
    containment escape — instead of two conditions that a reader could not tell apart.
    """
    reported = _reportable_path(artifact_candidate(project_root, relative_dir, ticket_id))
    if within_root(reported, project_root) is True:
        return reported
    return _reportable_path(project_root / relative_dir)


def _reportable_path(candidate: Path) -> Path:
    """The path an :class:`ArtifactRead` carries when the read never got started.

    Every outcome that reaches :func:`_read_json_artifact` reports the RESOLVED path,
    so the refusal branches must too or ``ArtifactRead.path`` means one thing on most
    answers and another on two of them — and a caller keying artifacts by it gets two
    keys for one file whenever the project root is relative or symlinked.

    THREE CONTAINMENT refusals arrive here, plus one ID refusal added later (case 4).
    The first three are the sub-cases
    :data:`~factory_console.domain.runs.ArtifactSkipReason` names in the SECOND of its two
    routes to ``unreadable`` — "the path could not be PROVEN to resolve inside the project
    root", i.e. an unresolvable path, an unresolvable root, or a resolved path that
    provably escaped. (Its first route, a file that exists and whose bytes would not read,
    never reaches here: that is decided on an open descriptor in
    :func:`_read_json_artifact`, which reports the path it already has.) They do not all
    resolve alike, so none may be assumed:

    1. the CANDIDATE could not be resolved (a symlink loop on the artifact itself). The
       retry below fails again and falls back to :meth:`Path.absolute`, which performs no
       filesystem lookup and so cannot fail the same way, rather than to the bare join;
    2. the candidate resolved and provably LEFT the root (a symlinked ``.factory``). The
       retry succeeds and this reports the escape TARGET — deliberately, per Amendment 1:
       a refusal names the path that could not be used, and the out-of-root target is that
       path;
    3. the ROOT could not be resolved, so containment was UNDECIDABLE
       (:func:`~factory_console.file_adapter.path_safety.within_root` answered ``None``).
       The candidate itself resolves fine, so the retry succeeds here too — but what it
       returns is an ordinary CONTAINED path, not an escape target. Only case 2 proves an
       escape, and only case 2 may be read as reporting one.
    4. the ID was refused as a path segment one layer up, so NOTHING was gated: the caller
       came through :func:`refusal_path`. This is not a containment sub-case at all. What
       arrives is either the ordinary contained candidate (a bare ``.``/``..`` id, whose
       ``.json`` suffix makes an in-root filename) or, when the id could not form an in-root
       name, the artifact DIRECTORY — because :func:`refusal_path` clamps first, precisely so
       this case can never borrow case 2's out-of-root spelling for something it has not
       proven. Like case 3, it must NOT be read as an escape target.

    An earlier revision of this docstring described only cases 1 and 2, which read case 3's
    perfectly ordinary path as "the escape target" — the same conflation of "provably
    outside" with "could not be checked" that
    :func:`~factory_console.file_adapter.path_safety.within_root` returns three values to
    keep apart.

    ``tests/unit/test_runs.py`` pins the reported path on cases 1-3;
    ``tests/unit/test_run_service.py`` pins case 4 through
    :class:`~factory_console.file_adapter.run_artifacts.RealRunArtifactReader`. Do not
    "simplify" this to an unconditional :meth:`Path.absolute` on the reading that resolution
    has already failed — it has not, on cases 2 and 3, and that would silently change what
    they report.
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
    the one the bytes come from. That sequence is the shared
    :func:`~factory_console.file_adapter.bounded_read.read_bounded` — see there for
    why it has exactly one copy rather than two kept in step by hand with
    :mod:`~factory_console.file_adapter.ledger`.

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
    result = read_bounded(path, max_bytes=MAX_ARTIFACT_BYTES, label="runs")
    if result.outcome == "not_found":
        # Nothing is there — no file, or a path component that is not a directory,
        # which amounts to the same thing. The ordinary state of a fresh clone, so
        # it is not logged: a project the factory has not run on is not a
        # degradation.
        return ArtifactRead(path=path, reason="absent")
    if result.outcome == "unreadable":
        return ArtifactRead(path=path, reason="unreadable")
    if result.outcome == "too_large":
        return ArtifactRead(path=path, reason="too_large")
    raw = result.data

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
