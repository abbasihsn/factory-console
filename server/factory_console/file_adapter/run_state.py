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

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

from factory_console.domain import TICKET_ID_PATTERN, RunState
from factory_console.domain.run_state_source import (
    RUN_STATE_SOURCE_LOCATIONS,
    JsonRunState,
    RunStateSource,
)
from factory_console.file_adapter.path_safety import PathTraversal

_LOGGER = logging.getLogger(__name__)

# On-disk run-state directory names in precedence order, highest wins. These are
# the literal directory names under the run-state dir (``in-flight`` hyphenated);
# each is mapped to its enum member BY VALUE via ``RunState(name)``, never by
# string guessing. See ARCHITECTURE.md "Factory run-state directory (read-only)".
_MARKER_PRECEDENCE = ("merged", "ready", "in-flight", "todo")

# The factory's nine ``FAC_STATES`` mapped to console states, explicitly and
# exhaustively. This is the ONE place a factory status name is interpreted: a
# status absent from this table is NOT munged into a member, it becomes
# ``unknown`` and is reported in ``JsonRunState.unrecognised``, so a factory that
# gains a tenth state surfaces as a visible gap instead of a repo full of
# ``unknown``. Three names (``todo``, ``ready``, ``merged``) are shared with the
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


def find_run_state_source(project_root: Path) -> RunStateSource | None:
    """Return the project's resolved run-state source, or ``None`` if it has none.

    Probes :data:`~factory_console.domain.run_state_source.RUN_STATE_SOURCE_LOCATIONS`
    in precedence order and returns the FIRST location present *in the form that
    location expects*:

    1. ``<project_root>/.factory/run-state.json`` (json) — what the factory writes.
    2. ``<project_root>/.factory/run-state`` (directory).
    3. ``<project_root>/docs/planning/.run-state`` (directory).

    The node type is checked, not merely existence (:meth:`Path.is_file` for the
    JSON form, :meth:`Path.is_dir` for the directory form), so a stray file where
    a directory belongs — or a directory named ``run-state.json`` — is skipped
    rather than resolved into a source that cannot be read.
    """
    for kind, relative in RUN_STATE_SOURCE_LOCATIONS:
        candidate = project_root / relative
        present = candidate.is_file() if kind == "json" else candidate.is_dir()
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
    layout — the same single-source guarantee both already share via
    :data:`RUN_STATE_RELATIVE_LOCATIONS`.
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
    must not share an answer at the call sites below. A run-state directory whose
    state subdirectories are traversable but not readable (mode ``0711``, or created
    by the factory under a different uid) passes every ``exists()``/``is_dir()``
    guard — those need only ``+x`` on the parent — while every ``iterdir()`` raises
    ``EACCES``. Read as ``False``, that made :func:`run_state_resolver` short-circuit
    to a constant mutable ``unknown`` for EVERY ticket, silently disabling the write
    gate on a project whose markers say ``merged``/``in-flight``. Read as ``None``,
    the caller falls back to probing the markers themselves, which ``exists()`` can
    still do.

    This is the directory form's answer to "does this source list anybody at all?" —
    the question that separates :attr:`RunState.absent` ("the source lists others and
    not you") from :attr:`RunState.unknown` ("the source names nobody, so it makes
    no claim about you"). A run-state directory that exists but contains no marker
    under any of :data:`_MARKER_PRECEDENCE` is VACUOUS, and a source that names
    nobody says nothing about anybody: every ticket must stay mutable, exactly as
    if there were no source at all. Without this, a freshly created (empty) run-state
    directory would resolve ``absent`` for every ticket and lock the whole project
    read-only — the same project-wide lockout the unreadable/vanished guards exist
    to prevent.

    Each state subdirectory is scanned only until its first TICKET marker
    (:func:`_is_ticket_marker_name`), so a populated run-state directory holding
    thousands of markers stops on the first entry. A directory holding only
    scaffolding is walked in full, because "no marker here" cannot be concluded
    earlier — that is the price of not counting ``.gitkeep`` as a ticket. A state
    subdirectory that is simply MISSING contributes no evidence either way and does
    not make the answer ``None``; only one that exists and refuses enumeration does.
    """
    saw_unreadable = False
    for state in _MARKER_PRECEDENCE:
        state_dir = run_state_dir / state
        try:
            for entry in state_dir.iterdir():
                if _is_ticket_marker_name(entry.name):
                    return True
        except FileNotFoundError:
            # A state subdirectory the factory has not created yet: ordinary, and
            # genuinely no evidence that the source lists anybody.
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
    it. ``.exists()`` covers a marker present as either a file or a directory, and
    needs only ``+x`` on the state subdirectory, so this still answers on a directory
    :func:`_directory_lists_any_ticket` could not enumerate.

    Callers MUST have validated ``ticket_id`` as a single path-safe segment first;
    this joins it onto a filesystem path. Propagates ``OSError`` — the caller decides
    what an unreadable directory means.
    """
    for state in _MARKER_PRECEDENCE:
        if (run_state_dir / state / ticket_id).exists():
            return RunState(state)
    return None


def _validate_ticket_id_as_segment(ticket_id: str) -> None:
    """Raise :class:`PathTraversal` unless ``ticket_id`` is one path-safe segment.

    Defense-in-depth for the directory form, where the id becomes a filesystem path
    segment: the id was already validated at the API boundary, but this module joins
    it onto a path, so it is re-validated at the point of use. ``fullmatch`` (not
    ``match``) so a trailing newline cannot sneak past the ``$`` anchor.
    :data:`TICKET_ID_PATTERN` allows ``.`` as a character, so bare ``.`` and ``..``
    pass the regex yet are single-segment traversals — reject them explicitly per the
    ARCHITECTURE run-state directory contract.
    """
    if re.fullmatch(TICKET_ID_PATTERN, ticket_id) is None or ticket_id in (".", ".."):
        raise PathTraversal(ticket_id)


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
      logged and resolves ``unknown`` for an id with no marker — but only AFTER the
      marker probe above, which needs just ``+x`` and so still returns the real
      state for a ticket the directory does name.
    - A ``run_state_dir`` that is no longer a directory when probed (it vanished or
      was atomically replaced between discovery in ``load_project`` and this call),
      or that cannot be read at all (e.g. the factory created it mode-0700 under a
      different uid, so stat'ing a marker raises ``EACCES``) -> :attr:`RunState.unknown`,
      NOT ``absent``. This mirrors the JSON form's ``readable=False`` rule in
      :func:`read_json_run_state`: a source that cannot be trusted must never be read
      as "definitively lists nothing", which here would turn a transient
      disappearance into a project-wide read-only lockout (every ticket ``absent``,
      so every write 409s) instead of the mutable ``unknown``.

      The ``OSError`` guard around the marker loop is what makes that mirroring real
      rather than partial: ``Path.exists()`` only swallows ``ENOENT``/``ENOTDIR``/
      ``EBADF``/``ELOOP``, so on ``EACCES`` it RAISES. Without the guard a
      permission-restricted run-state directory would escape this read-only prober
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

    _validate_ticket_id_as_segment(ticket_id)

    try:
        marker = _marker_state(run_state_dir, ticket_id)
        if marker is not None:
            return marker

        readable = run_state_dir.is_dir()
        # Settled AFTER the marker loop so the common (marker found) path never pays
        # for it: only an id the directory does not name has to ask whether the
        # directory names anybody.
        lists_any_ticket = _directory_lists_any_ticket(run_state_dir) if readable else False
    except OSError:
        # The directory cannot be stat'ed (EACCES and friends). Same answer as a
        # vanished directory below, for the same reason: an unreadable source is
        # "I could not tell", never "the source lists nothing".
        _LOGGER.warning(
            "run-state: %s could not be read; every ticket resolves unknown", run_state_dir
        )
        return RunState.unknown

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
        # this IS a degradation, and it is the one that quietly widens the write gate
        # (no marker + "could not tell" resolves to the mutable ``unknown``), so it
        # must leave a trace an operator can find.
        _LOGGER.warning(
            "run-state: %s could not be enumerated; %s resolves unknown",
            run_state_dir,
            ticket_id,
        )
        return RunState.unknown
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
    table does not name maps to :attr:`RunState.unknown` AND is collected into
    ``unrecognised`` (de-duplicated, first-seen order): a tenth factory state must
    be visible as a named gap, never silently dropped. Every id that had SOME
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
    an unreadable file, unparseable JSON, a non-object document, an absent
    ``tickets`` key, and a ``tickets`` that is not an object (e.g. a list) all
    yield a :class:`JsonRunState` with ``readable=False``, i.e. :attr:`RunState.unknown`
    for every ticket queried, not :attr:`RunState.absent` — a file that cannot be
    trusted must not be read as "definitively lists nothing". Each case is logged
    at ``warning`` so the degradation leaves a trace. Individual entries that are
    not objects, or that carry a non-string ``status``, are skipped the same way
    without discarding the entries that are fine — but their id still lands in
    ``known_ticket_ids``, so they resolve ``unknown`` (an entry we could not
    classify), not ``absent`` (no entry at all).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        _LOGGER.warning("run-state: %s could not be read; every ticket resolves unknown", path)
        return JsonRunState(readable=False)
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
    for ticket_id, entry in tickets.items():
        known_ticket_ids.add(ticket_id)
        status = entry.get("status") if isinstance(entry, dict) else None
        if not isinstance(status, str):
            # ``%r`` (like the status below), never ``%s``: this key is arbitrary
            # text from a file the console does not own, and the log formatter is
            # one record per line — an unescaped newline in it would forge log
            # records (e.g. a fake write-audit line).
            _LOGGER.warning("run-state: %s entry %r has no string status", path, ticket_id)
            continue
        state = FACTORY_STATUS_ALIASES.get(status)
        if state is None:
            if status not in unrecognised:
                unrecognised.append(status)
            _LOGGER.warning(
                "run-state: %s names status %r, which this console does not know",
                path,
                status,
            )
            continue
        states[ticket_id] = state
    return JsonRunState(
        states=states, unrecognised=unrecognised, known_ticket_ids=frozenset(known_ticket_ids)
    )


def probe_ticket_state_from_source(source: RunStateSource | None, ticket_id: str) -> RunState:
    """Resolve ``ticket_id``'s :class:`RunState` from the project's run-state source.

    The source-aware entry point every caller should use; it dispatches on
    ``source.kind`` so a JSON-sourced project and a directory-sourced one resolve
    through the same call:

    - ``source is None`` -> :attr:`RunState.unknown` — there is no source to ask.
    - ``kind == "json"`` -> the ticket's entry in the parsed file when one maps
      to a known state; :attr:`RunState.unknown` when the file could not be
      trusted at all, when its ``tickets`` object parsed fine and is EMPTY (a
      vacuous source lists nobody, so it claims nothing about anybody), OR when
      the id has an entry whose status this console does not recognise;
      :attr:`RunState.absent` when the file parsed fine, lists at least one
      ticket, and simply has no entry for THIS id — the source resolved and
      answered "not listed".
    - ``kind == "directory"`` -> the marker precedence :func:`probe_ticket_state`
      reads (via the shared :func:`_marker_state`): :attr:`RunState.absent` when the
      directory lists other tickets but no marker names this id,
      :attr:`RunState.unknown` when it holds no marker for any id at all (the same
      vacuous rule) or when its state subdirectories could not be enumerated to tell,
      unchanged otherwise.

    Resolving one ticket re-reads the source, matching ``ARCHITECTURE.md``
    "every request re-reads"; a caller resolving MANY tickets should take a
    :func:`run_state_resolver` instead, which reads the JSON once.

    Raises:
        PathTraversal: only on the directory path, where ``ticket_id`` becomes a
            filesystem path segment, and only when that directory is actually
            probed: :func:`run_state_resolver` settles the SOURCE-level questions
            (unreadable, vacuous) before any id is validated, so a directory that
            answers ``unknown`` for every ticket answers it for an unsafe id too,
            without raising. The JSON path joins no path, so it looks an unsafe id
            up as an ordinary key and answers ``unknown``/``absent`` like any other
            unrecognised id, never raising.
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
    is left to the closure. That is what keeps the two kinds symmetric in log volume:
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
    the same :func:`_marker_state`, so the two forms cannot answer differently.
    """
    if source is None:
        return lambda _ticket_id: RunState.unknown
    if source.kind == "json":
        parsed = read_json_run_state(source.path)

        # A ``tickets`` object that parsed fine and is EMPTY is a vacuous source: it
        # lists nobody, so it makes no claim about anybody. Settled once here (like
        # ``readable``) rather than re-derived per ticket. ``known_ticket_ids`` is
        # empty iff ``tickets`` was empty, so this is exactly "the file resolved and
        # names no ticket at all".
        vacuous = not parsed.known_ticket_ids

        def resolve_json(ticket_id: str) -> RunState:
            if ticket_id in parsed.states:
                return parsed.states[ticket_id]
            if not parsed.readable or vacuous or ticket_id in parsed.known_ticket_ids:
                return RunState.unknown
            return RunState.absent

        return resolve_json

    try:
        # ``is_dir()`` alone is not enough: it stats the directory ENTRY from the
        # parent, which still succeeds for a directory the console has no search
        # permission on. Stat one path INSIDE it — the same shape the marker loop
        # probes — so an EACCES surfaces here, once, instead of once per ticket.
        directory_readable = source.path.is_dir()
        if directory_readable:
            # Called for the OSError, not the answer — bind it so this does not read
            # as a no-op line someone deletes.
            _ = (source.path / _MARKER_PRECEDENCE[0]).exists()
    except OSError:
        directory_readable = False
    if not directory_readable:
        _LOGGER.warning(
            "run-state: %s is not a readable directory; every ticket resolves unknown", source.path
        )
        return lambda _ticket_id: RunState.unknown

    # The directory form's vacuous question, settled ONCE for the same reason
    # ``readable`` is (and the same reason the JSON form settles it once): a
    # whole-manifest projection must not re-scan the state subdirectories per ticket
    # to learn what the source lists. ``probe_ticket_state`` keeps its own equivalent
    # checks for direct single-ticket callers; both reach the per-id answer through
    # ``_marker_state``, so the two forms cannot answer differently.
    lists_any_ticket = _directory_lists_any_ticket(source.path)
    if lists_any_ticket is False:
        # Definitively lists nobody: it claims nothing about anybody.
        return lambda _ticket_id: RunState.unknown
    if lists_any_ticket is None:
        # "I could not tell" — NOT "it lists nobody". Answering a constant ``unknown``
        # here would put every ticket in the mutable set and silently disable the write
        # gate on a project whose markers say ``merged``. Fall through to the per-ticket
        # probe instead: ``_marker_state`` only needs ``+x``, so a ticket the directory
        # DOES name still resolves its real, read-only state, and only an id with no
        # marker gets the mutable ``unknown``. Logged once per resolver, like the
        # unreadable case above.
        _LOGGER.warning(
            "run-state: %s could not be enumerated; only tickets with a marker resolve",
            source.path,
        )

    lists_someone = lists_any_ticket is True

    def resolve_directory(ticket_id: str) -> RunState:
        _validate_ticket_id_as_segment(ticket_id)
        try:
            marker = _marker_state(source.path, ticket_id)
        except OSError:
            _LOGGER.warning(
                "run-state: %s could not be read; %s resolves unknown", source.path, ticket_id
            )
            return RunState.unknown
        if marker is not None:
            return marker
        # No marker: ``absent`` only when the source is known to list SOMEONE. When
        # vacuity is unknowable (``lists_any_ticket is None``) this is the mutable
        # ``unknown``, matching ``probe_ticket_state``.
        return RunState.absent if lists_someone else RunState.unknown

    return resolve_directory
