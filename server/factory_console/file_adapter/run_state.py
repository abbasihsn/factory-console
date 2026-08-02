# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.
"""Read the project's run-state artifact to resolve a ticket's :class:`RunState`.

Run-state is authoritative for whether a ticket is mutable and drives the
``RunState`` badge in the console. TWO on-disk forms are read, in this order:

1. ``.factory/run-state.json`` — what the App Factory writes today:
   ``{"version": int, "tickets": {ID: {"status": str, "pr_url": str|null}},
   "parts_landed": object}``, with ``status`` drawn from the factory's nine
   ``FAC_STATES``.
2. The legacy run-state DIRECTORY of per-state marker subdirectories (see
   ``ARCHITECTURE.md`` "Factory run-state directory (read-only)").

:func:`find_run_state_source` resolves WHICH form a project has;
:func:`probe_ticket_state_from_source` dispatches on it. Every external name —
a JSON ``status``, a marker directory name — becomes a :class:`RunState` through
an explicit table (:data:`FACTORY_STATUS_ALIASES`, :data:`_MARKER_PRECEDENCE`),
never by string munging, because the factory's ``in_progress`` and the directory
form's ``in-flight`` differ by exactly the character a ``.replace()`` would
paper over.

The console MUST NOT write, create, or delete anything here — a guard test
asserts this module contains no filesystem-mutating call.
"""

from __future__ import annotations

import errno
import json
import logging
import re
import stat
from collections.abc import Callable
from pathlib import Path

from factory_console.domain import TICKET_ID_PATTERN, RunState
from factory_console.domain.run_state_source import (
    RUN_STATE_SOURCE_LOCATIONS,
    JsonRunState,
    RunStateSource,
)
from factory_console.file_adapter.path_safety import (
    # Re-exported, not used here: this module stopped RAISING :class:`PathTraversal`
    # directly when the segment rule moved to
    # :mod:`~factory_console.file_adapter.path_safety`, but callers and tests import it
    # FROM here as part of this module's surface, and one shared class is the whole
    # point of the uniform ``invalid_ticket_id`` contract. The ``noqa`` is what keeps a
    # lint autofix from quietly deleting a name other modules import; an ``__all__``
    # would do the same job while inventing a hand-maintained export list no sibling
    # file_adapter module has, which is a drift risk of its own.
    PathTraversal,  # noqa: F401
    validate_ticket_id_as_segment,
)

_LOGGER = logging.getLogger(__name__)

# On-disk run-state directory names in precedence order, highest wins. These are
# the literal directory names under the run-state dir (``in-flight`` hyphenated);
# each is mapped to its enum member BY VALUE via ``RunState(name)``, never by
# string guessing. See ARCHITECTURE.md "Factory run-state directory (read-only)".
_MARKER_PRECEDENCE = ("merged", "ready", "in-flight", "todo")

# The factory's nine ``FAC_STATES`` mapped to console states, explicitly and
# exhaustively. This is the ONE place a factory status name is interpreted: a
# status absent from this table is NOT munged into a member. It is recorded in
# ``JsonRunState.unrecognised`` (the value, once, for the whole file) and in
# ``JsonRunState.unclassifiable`` (per ticket id), and the ticket it names resolves
# the REFUSING :attr:`RunState.unreadable`.
#
# That refusal is T80 amendment 4, and it overturns what this comment said before:
# the unrecognised value used to be routed to the MUTABLE ``unknown``, so a tenth
# factory state surfaced only as a ``warning`` log line while the write gate GRANTED
# the edit. The old answer looked reasonable because ``unrecognised`` was described
# as keeping the gap "visible" — but naming a gap in a log line and then ignoring it
# at the only point that acts on it is not visibility. An entry that names THIS
# ticket under a status this console cannot classify is "the source claims, and we
# could not see what", which the RESOLUTION INVARIANT (restated by amendment 4:
# refuse whenever the information needed is UNAVAILABLE, whether unread or read and
# uninterpretable) requires be refused. The concrete failure it closes: the factory
# adds ``in_review``, this console does not know it, and a ticket a lane is actively
# reviewing reads as editable.
#
# ``unrecognised`` is still collected, and that is not redundant with the refusal —
# amendment 4 requires BOTH ("a fix that refuses while dropping the name has traded
# one silence for another"), because the refusal tells an operator about one ticket
# while the collected value tells them their console is a version behind the factory.
#
# Three names (``todo``, ``ready``, ``merged``) are shared with the
# directory form; the other six exist only here. Note ``in_progress`` maps to
# ``RunState.in_progress`` and NOT to the directory form's ``in-flight``: the
# factory has no ``in-flight``, and collapsing the two would lose which source
# said what.
FACTORY_STATUS_ALIASES: dict[str, RunState] = {
    "todo": RunState.todo,
    "in_progress": RunState.in_progress,
    "ready": RunState.ready,
    "in_part": RunState.in_part,
    "in_submilestone": RunState.in_submilestone,
    "merged": RunState.merged,
    "flagged": RunState.flagged,
    "failed": RunState.failed,
    "needs_human": RunState.needs_human,
}

# The documented run-state DIRECTORY locations, project-relative, in fallback
# order (highest precedence first) — the directory subset of
# :data:`~factory_console.domain.run_state_source.RUN_STATE_SOURCE_LOCATIONS`,
# derived from it rather than restated so the two cannot drift. Single source of
# truth for WHERE the run-state dir can live: :func:`find_run_state_dir` probes
# these under a project root, and the T40 ``RealFileWatcher`` derives its
# run-state scope prefixes from the same tuple so the prober and the watcher
# cannot drift. See ARCHITECTURE.md "Factory run-state directory (read-only)".
RUN_STATE_RELATIVE_LOCATIONS: tuple[Path, ...] = tuple(
    relative for kind, relative in RUN_STATE_SOURCE_LOCATIONS if kind == "directory"
)


# The errno set that means "this node definitively is not there". DELIBERATELY NARROWER
# than CPython's ``pathlib._ignore_error``, which also swallows ``ELOOP``: a symlink loop
# — or a chain past ``MAXSYMLINKS`` — means the entry EXISTS and could not be RESOLVED,
# which is "I could not look", not "there is nothing to find". Nothing is lost by
# excluding it, because a DANGLING symlink already answers ``ENOENT`` on its own.
# Swallowing ``ELOOP`` reopened T80 amendment 3's fail-open through the errno table
# rather than through the walk: a looping ``merged/<id>`` answered ``False`` instead of
# raising, so :func:`_marker_state` stepped over it and returned a stale ``todo`` marker
# — the MUTABLE state — for a ticket the factory had merged; and a looping run-state
# directory answered ``False`` from :func:`_is_directory`, which :func:`run_state_resolver`
# reads as "not a directory" and turns into the mutable ``unknown`` for EVERY ticket in
# the project. ``EBADF`` stays: a path-based ``stat()`` cannot raise it, so it is inert
# either way, and dropping it would only invite someone to re-add ``ELOOP`` alongside it.
# Everything else (``EACCES`` above all) means "it may well be there and I could not
# look", which is the distinction T80's second amendment turns on.
_ABSENT_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR, errno.EBADF})


def _node_exists(path: Path) -> bool:
    """``True`` if ``path`` exists, ``False`` if it definitively does not, else RAISE.

    Semantically :meth:`Path.exists` — symlinks followed, a marker present as a file
    OR a directory both count — but with the errno split made HERE instead of inherited
    from the interpreter, which is what makes it a contract rather than an accident.

    :meth:`Path.exists` cannot carry that split portably. Through CPython 3.12 it
    ignores only ``_IGNORED_ERRNOS`` (``ENOENT``/``ENOTDIR``/``EBADF``/``ELOOP`` — one
    member wider than :data:`_ABSENT_ERRNOS`, see there for why ``ELOOP`` is not
    absence) and RE-RAISES ``EACCES``; from CPython 3.13 (gh-113978) it delegates to
    ``os.path.*``
    and swallows EVERY ``OSError``, answering ``False``. ``pyproject.toml`` declares
    ``requires-python = ">=3.11"`` with no upper bound, so both behaviours are inside
    the supported range — and on 3.13 the raise this module's entire ``unreadable``
    detection is built on would simply stop happening. A run-state directory the console
    cannot search would then read as "no marker here" and resolve the MUTABLE ``unknown``
    (or ``absent``, which still permits a delete) instead of the refusing
    :attr:`RunState.unreadable`: the write would be granted precisely BECAUSE the check
    could not run, which is the fail-open T80's second amendment exists to close. A
    silent interpreter upgrade must not be able to reopen it.

    ``ValueError`` answers ``False`` for parity with :meth:`Path.exists`, which treats a
    non-encodable path as absent rather than as an error.
    """
    try:
        path.stat()
    except OSError as exc:
        if exc.errno in _ABSENT_ERRNOS:
            return False
        raise
    except ValueError:
        return False
    return True


def _is_directory(path: Path) -> bool:
    """``True`` if ``path`` is a directory, ``False`` if it is definitively not, else RAISE.

    :meth:`Path.is_dir` with the same errno split :func:`_node_exists` makes, and for
    the same reason — see its docstring. A path that exists but is not a directory
    answers ``False`` without raising, exactly as :meth:`Path.is_dir` does, so only "I
    could not look" propagates.
    """
    try:
        return stat.S_ISDIR(path.stat().st_mode)
    except OSError as exc:
        if exc.errno in _ABSENT_ERRNOS:
            return False
        raise
    except ValueError:
        return False


def _is_regular_file(path: Path) -> bool:
    """``True`` if ``path`` is a regular file, ``False`` if it definitively is not, else RAISE.

    :meth:`Path.is_file` with the same errno split :func:`_node_exists` makes, and for
    the same reason — see its docstring. The JSON half of :func:`find_run_state_source`'s
    probe, so that discovering a source obeys the same "I could not look" rule as reading
    one.
    """
    try:
        return stat.S_ISREG(path.stat().st_mode)
    except OSError as exc:
        if exc.errno in _ABSENT_ERRNOS:
            return False
        raise
    except ValueError:
        return False


def find_run_state_source(project_root: Path) -> RunStateSource | None:
    """Return the project's resolved run-state source, or ``None`` if it has none.

    Probes :data:`~factory_console.domain.run_state_source.RUN_STATE_SOURCE_LOCATIONS`
    in precedence order and returns the FIRST location present *in the form that
    location expects*:

    1. ``<project_root>/.factory/run-state.json`` (json) — what the factory writes.
    2. ``<project_root>/.factory/run-state`` (directory).
    3. ``<project_root>/docs/planning/.run-state`` (directory).

    The node type is checked, not merely existence (:func:`_is_regular_file` for the
    JSON form, :func:`_is_directory` for the directory form), so a stray file where
    a directory belongs — or a directory named ``run-state.json`` — is skipped
    rather than resolved into a source that cannot be read.

    DISCOVERY IS BOUND BY THE SAME INVARIANT AS RESOLUTION (T80 amendment 3): a
    candidate that could not be PROBED — ``.factory`` itself mode ``0000``, so stat'ing
    the file inside it raises ``EACCES`` — resolves TO that candidate rather than being
    skipped, and the read path then answers the refusing :attr:`RunState.unreadable` for
    every ticket. Skipping it would fall through to a lower-precedence location, or to
    ``None``, and ``None`` is the MUTABLE ``unknown`` for every ticket in the project —
    a write granted precisely because the check could not run, from the one step that
    runs before any of the checks. Probing a candidate we could not look at as though
    it were absent is the "I could not look" / "there is nothing to find" conflation this
    ticket has now been amended for four times, one step further upstream.

    This is also why the probe goes through :func:`_is_regular_file` /
    :func:`_is_directory` rather than :meth:`Path.is_file` / :meth:`Path.is_dir`: those
    two RE-RAISE ``EACCES`` through CPython 3.12 and SWALLOW it from 3.13 (gh-113978, see
    :func:`_node_exists`), so on the older interpreter an unreadable ``.factory`` escaped
    this read-only prober as an unmapped 500 on every request, and on the newer one it
    would silently become "this project has no run-state" — a crash and a fail-open from
    one line, neither of them a decision. The helpers make the answer the interpreter's
    to report and this module's to decide.
    """
    for kind, relative in RUN_STATE_SOURCE_LOCATIONS:
        candidate = project_root / relative
        try:
            present = _is_regular_file(candidate) if kind == "json" else _is_directory(candidate)
        except OSError:
            _LOGGER.warning(
                "run-state: %s could not be probed; it is treated as the project's source "
                "and every ticket resolves unreadable and is refused a write",
                candidate,
            )
            return RunStateSource(kind=kind, path=candidate)
        if present:
            return RunStateSource(kind=kind, path=candidate)
    return None


def find_run_state_dir(project_root: Path) -> Path | None:
    """Return the project's run-state DIRECTORY, or ``None`` if none is present.

    A thin wrapper over :func:`find_run_state_source` keeping the original
    directory-only contract for callers that specifically mean "the marker
    directory" (the writer's forbidden-paths guard, the watcher's scope): a
    resolved JSON source yields ``None`` here, because there is no directory. Use
    :func:`find_run_state_source` to read run-state.
    """
    source = find_run_state_source(project_root)
    if source is None or source.kind != "directory":
        return None
    return source.path


def is_run_state_marker(rel_path: str) -> bool:
    """True if ``rel_path`` (project-relative, POSIX) names a run-state marker.

    A marker lives exactly two segments below a run-state location —
    ``<location>/<state>/<ticket_id>`` — which is the layout
    :func:`probe_ticket_state` reads (``run_state_dir / state / ticket_id``).
    Owning that structural rule here, next to the locations it belongs to, keeps
    the T40 watcher's marker detection from drifting from the prober's marker
    LAYOUT — the same single-source guarantee both already share via
    :data:`RUN_STATE_RELATIVE_LOCATIONS`.

    That guarantee is about layout ONLY, and deliberately does not extend to the
    marker's NAME. :func:`_is_ticket_marker_name` — the separate rule the vacuity
    scan uses — additionally requires the last segment to look like a ticket id and
    to not begin with a dot, so ``<location>/todo/.gitkeep`` is a marker HERE (the
    watcher refreshes on it, which costs only a redundant re-read) and is NOT one
    there (it must not make an empty run-state directory authoritative). Widening
    this function to match would be safe; narrowing THAT one would not.
    """
    for location in RUN_STATE_RELATIVE_LOCATIONS:
        prefix = location.as_posix()
        if rel_path.startswith(prefix + "/"):
            remainder = rel_path[len(prefix) + 1 :]
            return remainder.count("/") == 1
    return False


def _is_ticket_marker_name(name: str) -> bool:
    """True if ``name`` (a directory entry under ``<run-state>/<state>/``) is a marker.

    A marker is named for a TICKET, so only an entry whose name could BE a ticket id
    counts as one. :data:`TICKET_ID_PATTERN` admits ``.``, so it alone would accept
    ``.gitkeep`` — the one way to commit an otherwise-empty state subdirectory to
    git — as well as ``.DS_Store`` and editor swap files; a leading dot is therefore
    excluded explicitly, the same way :func:`probe_ticket_state` rejects the bare
    ``.``/``..`` ids the pattern also admits. This filter is what keeps
    :func:`_directory_lists_any_ticket` answering "does this source list a TICKET?"
    rather than "does this directory contain a FILE?" — without it, one scaffolding
    placeholder would make an otherwise-empty run-state directory non-vacuous and
    resolve every ticket ``absent``, i.e. exactly the project-wide read-only lockout
    the vacuous rule exists to prevent (T80 amendment, gap 1).

    It is deliberately a name-shape test and not a manifest lookup: this module is
    the read-only run-state prober and has no manifest to consult. A non-dot,
    pattern-matching stray (``README``) still counts as a marker; that is the
    residual, and it errs toward the pre-amendment behaviour rather than toward a
    new one.
    """
    return not name.startswith(".") and re.fullmatch(TICKET_ID_PATTERN, name) is not None


def _directory_lists_any_ticket(run_state_dir: Path) -> bool | None:
    """Does ``run_state_dir`` hold at least ONE marker, for any id, in any state?

    Returns ``True`` (it lists somebody), ``False`` (it definitively lists nobody),
    or ``None`` for "I could not tell" — at least one state subdirectory exists but
    could not be enumerated. The three-way return is the point: collapsing ``None``
    into ``False`` would report an UNREADABLE source as a VACUOUS one, and the two
    now resolve to opposite gate answers (the refusing :attr:`RunState.unreadable`
    versus the mutable ``unknown``), so they must not share an answer at the call
    sites below. A run-state directory whose state subdirectories are traversable but
    not readable (mode ``0711``, or created by the factory under a different uid)
    passes every ``exists()``/``is_dir()`` guard — those need only ``+x`` on the
    parent — while every ``iterdir()`` raises ``EACCES``. Read as ``False``, that made
    :func:`run_state_resolver` short-circuit to a constant mutable ``unknown`` for
    EVERY ticket, silently disabling the write gate on a project whose markers say
    ``merged``/``in-flight``. Read as ``None``, the caller falls back to probing the
    markers themselves, which ``exists()`` can still do — so a ticket the directory
    DOES name still resolves its real state, and only an id with no readable marker
    is refused.

    This is the directory form's answer to "does this source list anybody at all?" —
    the question that separates :attr:`RunState.absent` ("the source lists others and
    not you") from :attr:`RunState.unknown` ("the source names nobody, so it makes
    no claim about you"). A run-state directory that exists but contains no marker
    under any of :data:`_MARKER_PRECEDENCE` is VACUOUS, and a source that names
    nobody says nothing about anybody: every ticket must stay mutable, exactly as
    if there were no source at all. Without this, a freshly created (empty) run-state
    directory would resolve ``absent`` for every ticket and lock the whole project
    read-only — the same project-wide lockout the vanished guard exists to prevent.
    An UNREADABLE source (``None``) is the one case where a project-wide refusal is
    the right answer, which is precisely why it must not be reported as vacuity.

    Each state subdirectory is FILTERED only until its first TICKET marker
    (:func:`_is_ticket_marker_name`), so a populated run-state directory holding
    thousands of markers stops matching names on the first one. That is a bound on
    the filtering, NOT on the directory read: :meth:`Path.iterdir` materialises the
    whole listing (``os.listdir``) before the first name is yielded, so the syscall
    cost is the same either way — read the early ``return`` as "answer as soon as
    the question is settled", not as an I/O optimisation. A directory holding only
    scaffolding is filtered in full, because "no marker here" cannot be concluded
    earlier — that is the price of not counting ``.gitkeep`` as a ticket. A state
    subdirectory that is simply MISSING — or that is not a directory at all —
    contributes no evidence either way and does not make the answer ``None``; only
    one that exists and refuses enumeration does.
    """
    saw_unreadable = False
    for state in _MARKER_PRECEDENCE:
        state_dir = run_state_dir / state
        try:
            for entry in state_dir.iterdir():
                if _is_ticket_marker_name(entry.name):
                    return True
        except (FileNotFoundError, NotADirectoryError):
            # A state subdirectory the factory has not created yet (``ENOENT``), or a
            # stray FILE where a state subdirectory belongs (``ENOTDIR``). Both are
            # ordinary and both are DEFINITIVE — there are no markers there — so they
            # are genuinely no evidence that the source lists anybody, and neither may
            # degrade the answer to ``None``. ``ENOTDIR`` in particular must not be
            # read as "could not be enumerated": that logs a degradation warning and
            # sends an operator chasing a permissions problem that does not exist.
            continue
        except OSError:
            # Exists but cannot be enumerated (EACCES and friends). NOT evidence of
            # emptiness — see the docstring.
            saw_unreadable = True
            continue
    return None if saw_unreadable else False


def _marker_state(run_state_dir: Path, ticket_id: str) -> RunState | None:
    """Return the state whose marker names ``ticket_id``, or ``None`` if none does.

    The marker-precedence lookup — ``merged`` > ``ready`` > ``in-flight`` > ``todo``,
    first hit wins, mapped to its enum member BY VALUE — factored out so
    :func:`probe_ticket_state` and :func:`run_state_resolver`'s directory closure
    share ONE implementation of "what does this directory say about this id?" while
    each settles the SOURCE-level questions (readable, vacuous) in the way that suits
    it. :func:`_node_exists` covers a marker present as either a file or a directory,
    and needs only ``+x`` on the state subdirectory, so this still answers on a directory
    :func:`_directory_lists_any_ticket` could not enumerate — the one degraded source
    a marker probe can still resolve honestly.

    ``OSError`` PROPAGATES, and the walk stops there. :func:`_node_exists` needs ``+x``
    on the state subdirectory and RAISES ``EACCES`` without it, so an error here means
    "a state that could have named this ticket could not be read", and the states are
    probed highest-precedence first. Both callers map the escaping ``OSError`` to the
    refusing :attr:`RunState.unreadable`, which is the answer T80's RESOLUTION INVARIANT
    (amendment 3) requires: a resolution that could not read something it needed must
    refuse, and may never fall back to a state MORE PERMISSIVE than the one it failed to
    check. "I looked and found nothing" and "I could not look" are different answers,
    and only the first may return a mutable state.

    The invariant's bound is "at or above the marker found", and the precedence walk
    satisfies it for free rather than by tracking which states failed: the loop returns
    on the FIRST readable hit, so every state it has already probed — and therefore every
    state that could have raised — outranks the hit. An unreadable directory BELOW an
    answer already determined is never even looked at (an unreadable ``todo/`` cannot
    change a readable ``merged/<id>``), and an unreadable directory ABOVE one refuses
    before the lower marker is reached. That is why this needs no error bookkeeping: the
    error and the hit can never both exist with the error at a lower precedence.

    An earlier revision continued past the error and returned the first readable hit,
    to keep one non-searchable ``merged/`` (mode ``0600``, or created by the factory
    under a different uid) from refusing every id in the project. That is availability
    bought with a fail-open, and it is exactly the trade the invariant forbids: a stale
    ``todo/<id>`` under an unreadable ``merged/`` reported the MUTABLE ``todo`` for a
    ticket the factory had already merged, silently, since no exception escaped to tell
    either caller. The project-wide refusal is now the correct cost — a refusal is
    recoverable by fixing the directory's permissions, an unnoticed write to a merged
    ticket is not. What the walk still does NOT do is turn one restricted state
    subdirectory into a source-level verdict: it is re-probed per ticket, so a project
    whose state subdirectories are merely unenumerable (mode ``0711`` — ``iterdir()``
    fails, ``stat()`` does not) still resolves each ticket the directory names to its
    real state.

    Callers MUST have validated ``ticket_id`` as a single path-safe segment first;
    this joins it onto a filesystem path.
    """
    for state in _MARKER_PRECEDENCE:
        if _node_exists(run_state_dir / state / ticket_id):
            return RunState(state)
    return None


def probe_ticket_state(run_state_dir: Path | None, ticket_id: str) -> RunState:
    """Resolve ``ticket_id``'s :class:`RunState` by probing ``run_state_dir``.

    Resolution rules (see ARCHITECTURE.md "Factory run-state directory
    (read-only)"):

    - ``run_state_dir is None`` (no run-state directory on disk) ->
      :attr:`RunState.unknown`.
    - Otherwise the ticket id is re-validated (defense-in-depth) and then the
      state directories are probed in precedence order ``merged`` > ``ready`` >
      ``in-flight`` > ``todo``; the first state whose ``<state>/<ticket_id>``
      marker exists (as a file OR a directory) wins, mapped to its enum member
      by value.
    - A present run-state directory that lists at least one OTHER ticket but has no
      matching marker for this one -> :attr:`RunState.absent` (the directory resolved,
      and it does not list this ticket).
    - A present run-state directory holding NO marker for ANY ticket under ANY state
      — a VACUOUS source — -> :attr:`RunState.unknown` for every id
      (:func:`_directory_lists_any_ticket`). The ``absent`` rule reasons about a
      source exercising authority over the tickets it lists; a source that names
      nobody exercises none, and answering ``absent`` there would turn an
      empty-but-valid run-state directory into a project-wide read-only lockout.
      A state subdirectory that exists but cannot be ENUMERATED is not read as
      empty: :func:`_directory_lists_any_ticket` answers ``None`` there, which is
      logged and resolves :attr:`RunState.unreadable` for an id with no marker — but
      only AFTER the marker probe above, which needs just ``+x`` and so still returns
      the real state for a ticket the directory does name.
    - A ``run_state_dir`` that is no longer a directory when probed (it vanished or
      was atomically replaced between discovery in ``load_project`` and this call)
      -> :attr:`RunState.unknown`, NOT ``absent``. This mirrors the JSON form's
      vanished-file rule in :func:`read_json_run_state`: a source that is not there
      must never be read as "definitively lists nothing", which would turn a
      transient disappearance into a project-wide read-only lockout (every ticket
      ``absent``, so every write 409s) instead of the mutable ``unknown``.
    - A ``run_state_dir`` that IS there and cannot be read at all (e.g. the factory
      created it mode-0700 under a different uid, so stat'ing a marker raises
      ``EACCES``) -> :attr:`RunState.unreadable`, which BOTH write gates refuse.
      This is the one case that does not join ``unknown``: a vanished source and a
      vacuous one are "I looked and there is nothing to find", while an unreadable
      one is "I could not look", and it may be hiding a ``merged`` marker. Failing
      open there would grant a write precisely BECAUSE the check could not run
      (T80 amendment 2); the refusal names the source path so an operator reads it
      as a permissions problem, not as "this ticket is not tracked".

      The ``OSError`` guard around the marker loop is what turns that into a
      decision rather than a crash: :func:`_node_exists` swallows only
      :data:`_ABSENT_ERRNOS` (``ENOENT``/``ENOTDIR``/``EBADF``), so on ``EACCES``
      — or ``ELOOP`` — it RAISES — a rule this module states itself rather than inheriting
      from :meth:`Path.exists`, whose errno handling changed in CPython 3.13 (see
      :func:`_node_exists`). Without the guard
      a permission-restricted run-state directory would escape this read-only prober
      as an unmapped 500 on every list/read/write request — the one outcome the
      "NEVER raises for a source-level problem" rule exists to prevent. Only
      :class:`PathTraversal`, raised above before any filesystem access, still
      propagates.

    Raises:
        PathTraversal: if ``ticket_id`` is not a single path-safe segment. This
            is raised BEFORE any filesystem lookup.
    """
    if run_state_dir is None:
        return RunState.unknown

    validate_ticket_id_as_segment(ticket_id)

    try:
        marker = _marker_state(run_state_dir, ticket_id)
        if marker is not None:
            return marker

        readable = _is_directory(run_state_dir)
        # Settled AFTER the marker loop so the common (marker found) path never pays
        # for it: only an id the directory does not name has to ask whether the
        # directory names anybody.
        lists_any_ticket = _directory_lists_any_ticket(run_state_dir) if readable else False
    except OSError:
        # Something under here EXISTS and cannot be read (EACCES and friends). NOT the
        # same answer as a vanished directory below: that one is "there is nothing to
        # find", this one is "I could not look", and only the second can be hiding a
        # ``merged`` marker. So it resolves the refusing ``unreadable`` — a permission
        # error must not be the reason a write is granted (T80 amendment 2).
        #
        # The message deliberately does not name WHICH node failed, and deliberately
        # does not say "every ticket" — matching the resolver's twin
        # (:func:`run_state_resolver`), which this must not drift from. This ``except``
        # covers ``_marker_state`` (a state subdirectory), the ``is_dir()`` re-check
        # (the run-state directory itself) and ``_directory_lists_any_ticket``, so
        # attributing an EACCES on the directory to "a state subdirectory" would send
        # an operator to chmod the wrong path. ``_marker_state`` reaches here only for
        # an id whose answer was not already settled by a HIGHER-precedence readable
        # marker (it returns on the first hit), so the refusal never overrides a state
        # this console did read — it covers exactly the ids whose real state could be
        # sitting in the directory that would not open (T80 amendment 3).
        _LOGGER.warning(
            "run-state: %s could not be read (the directory itself or one of its state "
            "subdirectories); %r resolves unreadable and is refused a write",
            run_state_dir,
            ticket_id,
        )
        return RunState.unreadable

    if not readable:
        # The directory went away after discovery: "I could not tell" (mutable
        # unknown), never "the source lists nothing" (absent, which refuses).
        _LOGGER.warning(
            "run-state: %s is no longer a directory; every ticket resolves unknown", run_state_dir
        )
        return RunState.unknown
    if lists_any_ticket is None:
        # The state subdirectories exist but refuse enumeration, so whether this
        # source lists anybody is unknowable. Logged, unlike the vacuous case below:
        # this IS a degradation, and an id with no readable marker in a source we
        # could not enumerate is the "I could not look" case, so it resolves the
        # refusing ``unreadable`` rather than the mutable ``unknown`` — the marker
        # this id needs may be sitting in the very subdirectory that would not open.
        # ``%r`` for the id, never ``%s`` — the module-wide convention for a value
        # that reaches a log record from outside (see ``read_json_run_state``), and
        # what the resolver's twin warnings already use.
        _LOGGER.warning(
            "run-state: %s could not be enumerated; %r resolves unreadable and is refused a write",
            run_state_dir,
            ticket_id,
        )
        return RunState.unreadable
    if not lists_any_ticket:
        # Vacuous source: it lists nobody, so it claims nothing about this id either.
        # Not logged — an empty run-state directory is an ordinary state for a project
        # the factory has not run on yet, not a degradation.
        return RunState.unknown
    return RunState.absent


def read_json_run_state(path: Path) -> JsonRunState:
    """Parse the factory's ``run-state.json`` at ``path`` into a :class:`JsonRunState`.

    Reads ``.tickets`` — ``{TICKET_ID: {"status": str, "pr_url": str|null}}`` —
    and maps each ``status`` through :data:`FACTORY_STATUS_ALIASES`. A status the
    table does not name is collected into ``unrecognised`` (de-duplicated, first-seen
    order) — a tenth factory state must be visible as a named gap, never silently
    dropped — and the ticket it names resolves the REFUSING :attr:`RunState.unreadable`
    (T80 amendment 4; it used to resolve the mutable ``unknown``, which let a status
    this console does not know read as editable). Every id that had SOME
    entry in ``tickets`` — mapped or not — is recorded in ``known_ticket_ids``, so
    a caller can tell that apart from an id with no entry at all. ``known_ticket_ids``
    is empty when ``tickets`` was an empty object — a readable file that lists nobody,
    which :func:`run_state_resolver` reads as vacuous and answers ``unknown`` for every
    id (a source that names nobody says nothing about anybody) — AND on every
    ``readable=False`` return below, where nothing could be parsed at all. The two are
    told apart by ``readable``, which :func:`run_state_resolver` checks first; read
    "empty ``known_ticket_ids``" as vacuous ONLY together with ``readable=True``.

    NEVER raises. A run-state file that cannot be trusted is a source-level
    problem, not a request failure — "I could not tell" is the honest answer — so
    a vanished file, non-UTF-8 bytes, unparseable JSON, a non-object document, an
    absent ``tickets`` key, and a ``tickets`` that is not an object (e.g. a list)
    all yield a :class:`JsonRunState` with ``readable=False``, i.e. :attr:`RunState.unknown`
    for every ticket queried, not :attr:`RunState.absent` — a file that cannot be
    trusted must not be read as "definitively lists nothing".

    ONE failure is not in that list, and it is the one that fails CLOSED: a file
    that EXISTS whose bytes could not be read (``OSError`` other than
    ``ENOENT``/``ENOTDIR`` — permission denied, an I/O error) additionally sets
    ``unreadable=True``, which :func:`run_state_resolver` turns into
    :attr:`RunState.unreadable` for every ticket, refused by BOTH write gates. "I
    could not look" is not "there is nothing to find": the file may name this ticket
    ``merged``, and granting a write because a permission error prevented the check
    would fail open (T80 amendment 2). A file that is simply GONE is not that case —
    nothing is there to be hiding anything — so it keeps the mutable ``unknown``,
    and so do the content failures above, whose bytes were read successfully and
    merely made no sense. Each case is logged
    at ``warning`` so the degradation leaves a trace.

    Individual entries that are not objects, or that carry a non-string ``status``, are
    skipped the same way without discarding the entries that are fine — but their id
    still lands in ``known_ticket_ids`` AND in ``unclassifiable``, so they resolve the
    refusing ``unreadable`` (this file said something about this ticket and we could not
    interpret it), never ``unknown`` (nothing was said) and never ``absent`` (no entry at
    all). Those two shapes are not hypothetical: the factory writes this file from
    another process, so a schema drift to ``{"T42": "merged"}`` — the status as the
    value rather than as a key of an object — reaches here as an ordinary parse.

    Note the asymmetry with the DOCUMENT-level content failures above, which keep the
    mutable ``unknown``: an unparseable document names no ticket, so it cannot be said
    to claim anything about the one being asked about, while an entry that names THIS
    id is a claim this console could not read. That boundary is where the restated
    invariant's "read and could not be interpreted" bites, and it is deliberate rather
    than an inconsistency — but see the ticket's open item 2, which asks whether the
    document-level half should move too.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        # The file is NOT THERE — it vanished between discovery and this read, or a
        # path component is not a directory, which amounts to the same thing. Nothing
        # exists to be unreadable, so this is indistinguishable from a project with no
        # source at all: the mutable ``unknown``, NOT ``unreadable``. Kept separate
        # from the ``OSError`` below for exactly that reason, and mirroring the
        # directory form's "vanished" rule (see :func:`probe_ticket_state`).
        _LOGGER.warning("run-state: %s is no longer present; every ticket resolves unknown", path)
        return JsonRunState(readable=False)
    except UnicodeDecodeError:
        # The bytes WERE read; they are not UTF-8. A content problem, like invalid
        # JSON below — the source answered, unintelligibly — so it keeps the mutable
        # ``unknown`` rather than the refusing ``unreadable``.
        _LOGGER.warning("run-state: %s is not valid UTF-8; every ticket resolves unknown", path)
        return JsonRunState(readable=False)
    except OSError:
        # The file EXISTS and its bytes could not be read (EACCES, EIO, EISDIR). "I
        # could not look", never "there is nothing to find": this resolves the
        # refusing ``unreadable`` for every ticket, because a source we cannot read
        # may be naming this ticket ``merged`` and granting the write because a
        # permission error stopped us from checking is failing OPEN (T80 amendment 2).
        _LOGGER.warning(
            "run-state: %s could not be read; every ticket resolves unreadable and is "
            "refused a write",
            path,
        )
        return JsonRunState(readable=False, unreadable=True)
    try:
        document = json.loads(raw)
    except (ValueError, RecursionError, MemoryError):
        # Not just ``JSONDecodeError`` (a ``ValueError`` subclass): this artifact
        # is written by another process, and ``json.loads`` answers pathological
        # input with exceptions outside that type — deeply nested arrays raise
        # ``RecursionError``, a huge document ``MemoryError``. Letting either
        # escape would break the "NEVER raises" contract above and 500 every
        # list/read/write request until the file changed.
        _LOGGER.warning("run-state: %s is not valid JSON; every ticket resolves unknown", path)
        return JsonRunState(readable=False)

    tickets = document.get("tickets") if isinstance(document, dict) else None
    if not isinstance(tickets, dict):
        _LOGGER.warning(
            "run-state: %s has no 'tickets' object (found %s); every ticket resolves unknown",
            path,
            type(tickets).__name__,
        )
        return JsonRunState(readable=False)

    states: dict[str, RunState] = {}
    unrecognised: list[str] = []
    known_ticket_ids: set[str] = set()
    # Per id, WHAT could not be classified — the phrase the refusal names (T80
    # amendment 4). Every id that lands here also lands in ``known_ticket_ids`` and
    # NOT in ``states``, which is the equivalence ``_resolve_json_state`` relies on:
    # it selects the refusal by ``known_ticket_ids`` membership, and looks the
    # description up here only to phrase it.
    unclassifiable: dict[str, str] = {}
    # Ids whose entry could not be classified, accumulated for ONE summary record per
    # parse instead of one per entry. How many entries this loop refuses is set by a
    # file the console does not own, and every gated write plus every list/deps/graph
    # projection re-parses it — so an unclassifiable status shared by 200 tickets would
    # otherwise emit 200 identical lines on every request, drowning the write-audit
    # records from ``_log_write``. This is the same log-once discipline the directory
    # form applies with ``reported_unreadable``/``reported_vanished`` in
    # :func:`run_state_resolver`; the two forms must not differ on it.
    not_an_object: list[str] = []
    no_string_status: list[str] = []
    for ticket_id, entry in tickets.items():
        known_ticket_ids.add(ticket_id)
        if not isinstance(entry, dict):
            # Schema drift: ``{"T42": "merged"}``, the status as the VALUE rather than
            # as a key of an object. The status may well be readable to a human, but
            # this console has no contract that says where to find it, so it is not
            # interpreted — guessing here would be exactly the string munging the
            # alias table exists to prevent.
            not_an_object.append(ticket_id)
            unclassifiable[ticket_id] = "an entry that is not an object"
            continue
        status = entry.get("status")
        if not isinstance(status, str):
            no_string_status.append(ticket_id)
            unclassifiable[ticket_id] = (
                "an entry with no status"
                if status is None
                else f"an entry whose status is not a string ({type(status).__name__})"
            )
            continue
        state = FACTORY_STATUS_ALIASES.get(status)
        if state is None:
            if status not in unrecognised:
                unrecognised.append(status)
                # Once per DISTINCT status, not once per entry that carries it: the
                # operator action is "teach the console this status", and the tenth
                # ticket naming it adds nothing the first did not already say.
                _LOGGER.warning(
                    "run-state: %s names status %r, which this console does not know",
                    path,
                    status,
                )
            unclassifiable[ticket_id] = f"status {status!r}"
            continue
        states[ticket_id] = state
    # ``%r`` (like every other externally sourced value logged in this module), never
    # ``%s``: these ids are arbitrary text from a file the console does not own, and
    # the log formatter is one record per line — an unescaped newline in one would
    # forge log records (e.g. a fake write-audit line).
    if not_an_object:
        _LOGGER.warning(
            "run-state: %s has %d entries that are not objects (first: %r)",
            path,
            len(not_an_object),
            not_an_object[0],
        )
    if no_string_status:
        _LOGGER.warning(
            "run-state: %s has %d entries with no string status (first: %r)",
            path,
            len(no_string_status),
            no_string_status[0],
        )
    return JsonRunState(
        states=states,
        unrecognised=unrecognised,
        known_ticket_ids=frozenset(known_ticket_ids),
        unclassifiable=unclassifiable,
    )


def _resolve_json_state(parsed: JsonRunState, ticket_id: str) -> RunState:
    """The JSON form's per-ticket answer, given ONE parse of the file.

    Pure and total: it consults only ``parsed``, so the single-ticket
    (:func:`probe_ticket_state_with_reason`) and whole-manifest
    (:func:`run_state_resolver`) callers reach the same answer from the same fields
    and cannot drift. The four arms, in order, are the four things a JSON source can
    have said about an id:

    1. Its bytes could not be read at all -> :attr:`RunState.unreadable`, refused by
       both gates (T80 amendment 2). Nothing was parsed, so no later arm can apply.
    2. It named a status this console maps -> that state.
    3. It said NOTHING this console can attribute to anybody — the file vanished, its
       content could not be parsed, or its ``tickets`` object resolved and is EMPTY —
       -> the mutable :attr:`RunState.unknown`. ``known_ticket_ids`` is empty iff
       ``tickets`` was empty, so the vacuity test is exactly "the file resolved and
       names no ticket at all", and a source that names nobody claims nothing about
       anybody.
    4. It LISTS this id and its entry could not be interpreted -> :attr:`RunState.unreadable`
       again (T80 amendment 4). This is the arm that changed: it used to answer the
       mutable ``unknown``, which let a status this console does not know — a tenth
       ``FAC_STATES`` member such as ``in_review`` — read as editable. The restated
       RESOLUTION INVARIANT is that a resolution refuses whenever the information it
       needed is UNAVAILABLE, whether because it could not be READ or because it was
       read and could not be INTERPRETED; "looked, saw, did not understand" is not
       silence, and only silence may return a mutable state. ``unknown`` is now
       exactly "nothing was said".

    Otherwise the file resolved, lists somebody, and does not list this id ->
    :attr:`RunState.absent`, which is refused an edit but stays deletable.

    Arms 1 and 4 share :attr:`RunState.unreadable` deliberately rather than splitting a
    fourth unnamed state off: amendment 4 widened the INVARIANT's wording, not the state
    set, and both arms mean "this console has no answer it may act on". They are told
    apart where the difference is actionable — in the refusal's prose, via
    :attr:`JsonRunState.unclassifiable` (see :func:`probe_ticket_state_with_reason`),
    because "fix the file's permissions" and "your console does not know this status"
    are different instructions to an operator.
    """
    if parsed.unreadable:
        return RunState.unreadable
    if ticket_id in parsed.states:
        return parsed.states[ticket_id]
    if not parsed.readable or not parsed.known_ticket_ids:
        return RunState.unknown
    if ticket_id in parsed.known_ticket_ids:
        return RunState.unreadable
    return RunState.absent


def probe_ticket_state_with_reason(
    source: RunStateSource | None, ticket_id: str
) -> tuple[RunState, str | None]:
    """:func:`probe_ticket_state_from_source`, plus WHY when the reason is nameable.

    Returns ``(state, unclassifiable)``. The second element is set only when a JSON
    source LISTS ``ticket_id`` under an entry this console could not interpret, and it
    is the operator-facing description of what the file said (``"status 'in_review'"``,
    ``"an entry with no status"``) — see :attr:`JsonRunState.unclassifiable`. It is
    ``None`` everywhere else, including for the OTHER route to
    :attr:`RunState.unreadable`, a source whose bytes could not be read at all: there
    is no value to name there, because nothing was parsed.

    This exists so
    :class:`~factory_console.file_adapter.write_gate.TicketNotMutable` can NAME the
    unrecognised value, which T80 amendment 4 requires ("an operator needs *the
    run-state says `in_review`, which this console does not know*, not *not
    tracked*"). It is a separate entry point rather than a widened
    :func:`probe_ticket_state_from_source` because only the write gate needs the
    reason; the read projections want the state alone.

    It reads the source ONCE — the same single parse
    :func:`probe_ticket_state_from_source` performs — by asking
    :func:`_resolve_json_state` for the state instead of re-deriving it, so the state
    and the reason are guaranteed to describe the same bytes and the refusal path costs
    no extra I/O and emits no duplicate warning. A directory source has no per-entry
    value to misread (a marker either names the id or does not), so it falls through to
    the ordinary resolver with no reason.

    Raises:
        PathTraversal: exactly as :func:`probe_ticket_state_from_source` — only on the
            directory path, and only when that directory is actually probed.
    """
    if source is not None and source.kind == "json":
        parsed = read_json_run_state(source.path)
        return _resolve_json_state(parsed, ticket_id), parsed.unclassifiable.get(ticket_id)
    return probe_ticket_state_from_source(source, ticket_id), None


def probe_ticket_state_from_source(source: RunStateSource | None, ticket_id: str) -> RunState:
    """Resolve ``ticket_id``'s :class:`RunState` from the project's run-state source.

    The source-aware entry point every caller should use; it dispatches on
    ``source.kind`` so a JSON-sourced project and a directory-sourced one resolve
    through the same call:

    - ``source is None`` -> :attr:`RunState.unknown` — there is no source to ask.
    - ``kind == "json"`` -> the four arms of :func:`_resolve_json_state`: the ticket's
      entry in the parsed file when one maps to a known state; :attr:`RunState.unknown`
      when the file vanished or its content could not be understood, or when its
      ``tickets`` object parsed fine and is EMPTY (a vacuous source lists nobody, so it
      claims nothing about anybody); :attr:`RunState.absent` when the file parsed fine,
      lists at least one ticket, and simply has no entry for THIS id — the source
      resolved and answered "not listed"; and :attr:`RunState.unreadable` for the two
      ways the answer is UNAVAILABLE — the file is there and its bytes could not be read
      at all, or it DOES list this id under an entry this console cannot interpret (an
      unrecognised status, a non-string status, an entry that is not an object). That
      last case used to answer the mutable ``unknown``; T80 amendment 4 refuses it, and
      :func:`probe_ticket_state_with_reason` is how the refusal names the value.
    - ``kind == "directory"`` -> the marker precedence :func:`probe_ticket_state`
      reads (via the shared :func:`_marker_state`): :attr:`RunState.absent` when the
      directory lists other tickets but no marker names this id,
      :attr:`RunState.unknown` when it holds no marker for any id at all (the same
      vacuous rule) or when it is no longer there, :attr:`RunState.unreadable` when
      it exists and could not be read or enumerated and no marker of a precedence the
      walk reached BEFORE the unreadable state answered for this id first (T80
      amendment 3 — an answer already determined by a higher precedence stands;
      anything below one this console could not read is refused), unchanged otherwise.

    :attr:`RunState.unreadable` is the only one of the three unnamed states that
    BOTH write gates refuse (T80 amendment 2): ``unknown`` and ``absent`` mean the
    source was consulted, while ``unreadable`` means it was not, and a write must
    never be granted because the check could not run.

    Resolving one ticket re-reads the source, matching ``ARCHITECTURE.md``
    "every request re-reads"; a caller resolving MANY tickets should take a
    :func:`run_state_resolver` instead, which reads the JSON once.

    Raises:
        PathTraversal: only on the directory path, where ``ticket_id`` becomes a
            filesystem path segment, and only when that directory is actually
            probed: :func:`run_state_resolver` settles the UNREADABLE-source question
            before any id is validated, so a directory the console cannot read at all
            answers ``unreadable`` for an unsafe id too, without raising. A VACUOUS
            directory does NOT get that treatment — it deliberately falls through to
            the per-ticket closure (see the NOTE in :func:`run_state_resolver`), which
            validates the id first and therefore DOES raise. The JSON path joins no
            path, so it looks an unsafe id up as an ordinary key and answers
            ``unknown``/``absent`` like any id the file does not list, never raising.
    """
    return run_state_resolver(source)(ticket_id)


def run_state_resolver(source: RunStateSource | None) -> Callable[[str], RunState]:
    """Return a ``ticket_id -> RunState`` resolver bound to ``source``, read ONCE.

    The batch form of :func:`probe_ticket_state_from_source`, for callers that
    resolve run-state for every ticket in a manifest (the list/deps/graph
    projection). A JSON source is parsed a single time and the returned closure
    answers from that parse, instead of re-reading and re-parsing the whole file
    once per ticket; a directory source keeps probing per ticket, which is what
    reading a marker layout means. Both forms funnel through this one function so
    the single-ticket and whole-manifest answers cannot disagree.

    BOTH source-level questions — "can this source be read at all?" and "does it list
    anybody?" — are settled once here, for both forms, and only the PER-TICKET question
    is left to the closure. For the JSON form that separation is total: one
    :func:`read_json_run_state` answers both, and the closure is
    :func:`_resolve_json_state` over that one parse — a pure function of the parsed
    result, which is also what :func:`probe_ticket_state_with_reason` calls, so the
    batch and single-ticket JSON answers are the same code and not merely the same
    intent. That keeps the two kinds symmetric in log volume:
    the JSON form reports an unreadable file once because it parses once, so the
    directory form must not report an unreadable directory once per ticket — a
    200-ticket list request against a run-state directory the console cannot stat would
    otherwise emit 200 identical warnings. It is also why the directory closure calls
    :func:`_marker_state` rather than :func:`probe_ticket_state`: the latter re-derives
    ``is_dir()`` AND re-scans the state subdirectories for every id with no marker, so
    delegating to it would turn "settled once" into O(tickets x states) directory
    listings on the list/deps/graph projection — the exact per-ticket re-scan this
    function exists to avoid. :func:`probe_ticket_state` keeps its own equivalent
    guards for direct single-ticket callers, and both reach the per-id answer through
    the same :func:`_marker_state`, so the two forms cannot answer differently FOR A
    SOURCE THAT IS NOT MUTATING UNDERNEATH THE REQUEST. That qualifier is load-bearing,
    not a hedge: vacuity is settled ONCE here while :func:`probe_ticket_state` re-derives
    it per call, so a marker set that CHANGES under a live resolver is not observed and
    the two forms then diverge in both directions. See the note inside
    ``resolve_directory`` below for the exact residual and for why it cannot reach the
    write gate.
    """
    if source is None:
        return lambda _ticket_id: RunState.unknown
    if source.kind == "json":
        parsed = read_json_run_state(source.path)
        return lambda ticket_id: _resolve_json_state(parsed, ticket_id)

    try:
        # The directory check alone is not enough: it stats the directory ENTRY from
        # the parent, which still succeeds for a directory the console has no search
        # permission on. Stat one path INSIDE it — the same shape the marker loop
        # probes — so an EACCES on the run-state directory ITSELF surfaces here, once,
        # instead of once per ticket. This settles only the whole-directory question:
        # a directory that is readable while one of its state subdirectories is not
        # cannot be decided here (see ``reported_unreadable`` below for why that case
        # must stay per ticket, and how its warning is still emitted only once).
        directory_present = _is_directory(source.path)
        if directory_present:
            # Called for the OSError, not the answer — bind it so this does not read
            # as a no-op line someone deletes.
            _ = _node_exists(source.path / _MARKER_PRECEDENCE[0])
    except OSError:
        # PRESENT but unreadable, and the two halves of the ``try`` say so in the same
        # way: ``_is_directory`` swallows only ``_ABSENT_ERRNOS``
        # (a directory that is not there answers ``False``, it does not raise), and the
        # canary only runs once it has already said the directory IS there.
        # So an ``OSError`` from either can only mean "it exists and I could not look"
        # — never "it vanished" — which is the split T80's second amendment turns on:
        # this resolves the refusing ``unreadable`` for every ticket, while the
        # vanished case below stays the mutable ``unknown``.
        _LOGGER.warning(
            "run-state: %s could not be read; every ticket resolves unreadable and is "
            "refused a write",
            source.path,
        )
        return lambda _ticket_id: RunState.unreadable
    if not directory_present:
        # The path is not a directory (any more): nothing is there to be hiding a
        # marker, so this is indistinguishable from a project with no source at all.
        _LOGGER.warning(
            "run-state: %s is not a directory; every ticket resolves unknown", source.path
        )
        return lambda _ticket_id: RunState.unknown

    # The directory form's vacuous question, settled ONCE for the same reason
    # ``readable`` is (and the same reason the JSON form settles it once): a
    # whole-manifest projection must not re-scan the state subdirectories per ticket
    # to learn what the source lists. ``probe_ticket_state`` keeps its own equivalent
    # checks for direct single-ticket callers; both reach the per-id answer through
    # ``_marker_state``, so the two forms cannot answer differently.
    lists_any_ticket = _directory_lists_any_ticket(source.path)
    # NOTE: a ``False`` (vacuous) answer deliberately does NOT short-circuit to a
    # constant ``unknown`` closure. Vacuity is a statement about what the ENUMERATION
    # found, and enumeration and the marker probe do not recognise the same names:
    # ``_is_ticket_marker_name`` skips every dot-leading entry (so ``.gitkeep`` cannot
    # make a directory authoritative), while ``validate_ticket_id_as_segment`` admits
    # a dot-leading ticket id. A directory whose only marker is ``merged/.spike`` there-
    # fore enumerates as vacuous while ``_marker_state`` can still name it ``merged``.
    # Short-circuiting would answer the mutable ``unknown`` for that id and hand the
    # write gate an edit on a ticket a lane owns — and it would freeze vacuity for the
    # resolver's whole life, so a marker written mid-request went unseen. Falling
    # through costs one ``exists()`` per state on a directory that is empty by
    # definition, and keeps this form answering through the same ``_marker_state`` as
    # ``probe_ticket_state``, which is what makes the two provably agree.
    if lists_any_ticket is None:
        # "I could not tell" — NOT "it lists nobody". Answering a constant ``unknown``
        # here would put every ticket in the mutable set and silently disable the write
        # gate on a project whose markers say ``merged``. Fall through to the per-ticket
        # probe instead: ``_marker_state`` only needs ``+x``, so a ticket the directory
        # DOES name still resolves its real, read-only state, and only an id with no
        # marker gets the refusing ``unreadable``. Logged once per resolver, like the
        # unreadable-source case above.
        _LOGGER.warning(
            "run-state: %s could not be enumerated; tickets with no readable marker "
            "resolve unreadable and are refused a write",
            source.path,
        )

    lists_someone = lists_any_ticket is True
    # Separate from ``lists_someone``: an unenumerable source cannot answer "does it
    # list you?" at all, so an id with no marker is "I could not look" (refused), not
    # "it lists nobody" (mutable). Carried into the closure rather than re-derived,
    # since vacuity is settled once here.
    enumeration_failed = lists_any_ticket is None
    # The readability canary above stats only ``_MARKER_PRECEDENCE[0]``, so it catches
    # an unreadable run-state dir but NOT one whose individual state subdirectories
    # differ (``merged`` readable, ``ready`` mode-0000). That case can only surface per
    # ticket, inside ``_marker_state`` — and it must stay per ticket, because widening
    # the canary to every state would answer a constant ``unreadable`` for the whole
    # project the moment one subdirectory is restricted, refusing even the ids whose
    # answer ``_marker_state`` reaches BEFORE the restricted state (a readable
    # ``merged/<id>`` outranks an unreadable ``ready/``, and the walk returns on the
    # first hit) and the ids in a merely unenumerable source (mode ``0711``, where
    # every marker still stats). What must not be per ticket is
    # the WARNING: a 200-ticket projection would otherwise emit 200 identical lines,
    # breaking this function's "settled once, logged once" guarantee exactly when an
    # operator needs one clear signal. So the degradation is reported once per resolver.
    reported_unreadable = False
    reported_vanished = False

    def resolve_directory(ticket_id: str) -> RunState:
        nonlocal reported_unreadable, reported_vanished
        validate_ticket_id_as_segment(ticket_id)
        try:
            marker = _marker_state(source.path, ticket_id)
            if marker is not None:
                return marker
            # Re-confirm the source is STILL a directory before answering ``absent``.
            # ``lists_someone`` was settled once, at resolver construction; the answer
            # it licenses ("the source lists others, so it definitively does not list
            # you") stops being true the moment the source goes away. A projection
            # holds one resolver for a whole list/deps/graph request, so a factory that
            # rewrites run-state mid-request (rm+recreate, or an atomic rename swap)
            # leaves every remaining id with no marker — and answering ``absent`` there
            # would turn a transient disappearance into a project-wide read-only
            # lockout, the very outcome ``probe_ticket_state``'s own ``is_dir()``
            # re-check exists to prevent. Paid only on the no-marker path, so a ticket
            # the directory names never stats for it.
            #
            # This closes the DIRECTORY-level divergence only. Vacuity itself is still
            # settled once, by design — re-deriving it per ticket is the O(tickets x
            # states) re-scan this whole function exists to avoid — so a marker set that
            # CHANGES under a live resolver is not observed, and the divergence runs in
            # BOTH directions: markers deleted after construction make this form answer
            # ``absent`` where the re-scanning :func:`probe_ticket_state` answers
            # ``unknown``, and markers ADDED to a directory that was vacuous at
            # construction make this form answer the mutable ``unknown`` where the probe
            # answers ``absent``. So read the "the two forms cannot answer differently"
            # guarantee above as scoped to a source that is not mutating underneath the
            # request.
            #
            # That residual cannot reach the WRITE GATE, which is what makes it
            # acceptable rather than something to pay for: ``ensure_mutable``/
            # ``ensure_deletable`` resolve through
            # :func:`probe_ticket_state_from_source`, which builds a FRESH resolver for
            # every call, so ``lists_someone`` is never stale at gate time. The only
            # long-lived resolver is ``RealFileAdapter._projection_for``'s, and it feeds
            # the read-only list/deps/graph badges — where a stale badge for the length
            # of one request is cosmetic, and the next request re-reads.
            still_a_directory = _is_directory(source.path)
        except OSError:
            if not reported_unreadable:
                reported_unreadable = True
                # Deliberately does not name WHICH node failed: this ``except`` covers
                # both ``_marker_state`` (a state subdirectory) and the ``is_dir()``
                # re-check (the run-state directory itself), and attributing an EACCES
                # on the latter to "a state subdirectory" would send an operator to
                # chmod the wrong path.
                _LOGGER.warning(
                    "run-state: %s could not be read (the directory itself or one of "
                    "its state subdirectories); every ticket with no readable marker "
                    "(first: %r) resolves unreadable and is refused a write",
                    source.path,
                    ticket_id,
                )
            return RunState.unreadable
        if not still_a_directory:
            # Logged once per resolver, like every other degradation settled here: a
            # 200-ticket projection must not emit 200 identical lines.
            if not reported_vanished:
                reported_vanished = True
                _LOGGER.warning(
                    "run-state: %s is no longer a directory; every ticket with no "
                    "marker (first: %r) resolves unknown",
                    source.path,
                    ticket_id,
                )
            return RunState.unknown
        # No marker: ``absent`` only when the source is known to list SOMEONE. When
        # the enumeration FAILED this is the refusing ``unreadable`` — the marker this
        # id needs may be in the subdirectory that would not open — and when the source
        # genuinely lists nobody it is the mutable ``unknown``. All three match
        # ``probe_ticket_state``'s tail, which the two forms must not drift apart on.
        if lists_someone:
            return RunState.absent
        return RunState.unreadable if enumeration_failed else RunState.unknown

    return resolve_directory
