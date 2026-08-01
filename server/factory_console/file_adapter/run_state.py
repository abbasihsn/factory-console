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
    - A present run-state directory with no matching marker -> :attr:`RunState.absent`
      (the directory resolved, and it does not list this ticket).

    Raises:
        PathTraversal: if ``ticket_id`` is not a single path-safe segment. This
            is raised BEFORE any filesystem lookup.
    """
    if run_state_dir is None:
        return RunState.unknown

    # Defense-in-depth: this id was already validated at the API boundary, but it
    # is about to be used as a filesystem path segment, so re-validate here.
    # ``fullmatch`` (not ``match``) so a trailing newline cannot sneak past the
    # ``$`` anchor. TICKET_ID_PATTERN allows ``.`` as a character, so bare ``.``
    # and ``..`` pass the regex yet are single-segment traversals — reject them
    # explicitly per the ARCHITECTURE run-state directory contract.
    if re.fullmatch(TICKET_ID_PATTERN, ticket_id) is None or ticket_id in (".", ".."):
        raise PathTraversal(ticket_id)

    for state in _MARKER_PRECEDENCE:
        # ``.exists()`` covers a marker present as either a file or a directory.
        if (run_state_dir / state / ticket_id).exists():
            return RunState(state)

    return RunState.absent


def read_json_run_state(path: Path) -> JsonRunState:
    """Parse the factory's ``run-state.json`` at ``path`` into a :class:`JsonRunState`.

    Reads ``.tickets`` — ``{TICKET_ID: {"status": str, "pr_url": str|null}}`` —
    and maps each ``status`` through :data:`FACTORY_STATUS_ALIASES`. A status the
    table does not name maps to :attr:`RunState.unknown` AND is collected into
    ``unrecognised`` (de-duplicated, first-seen order): a tenth factory state must
    be visible as a named gap, never silently dropped. Every id that had SOME
    entry in ``tickets`` — mapped or not — is recorded in ``known_ticket_ids``, so
    a caller can tell that apart from an id with no entry at all.

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
      trusted at all OR the id has an entry whose status this console does not
      recognise; :attr:`RunState.absent` when the file parsed fine and simply
      has no entry for the id — the source resolved and answered "not listed".
    - ``kind == "directory"`` -> :func:`probe_ticket_state`: :attr:`RunState.absent`
      when the directory is present but no marker names the id, unchanged
      otherwise.

    Resolving one ticket re-reads the source, matching ``ARCHITECTURE.md``
    "every request re-reads"; a caller resolving MANY tickets should take a
    :func:`run_state_resolver` instead, which reads the JSON once.

    Raises:
        PathTraversal: only on the directory path, where ``ticket_id`` becomes a
            filesystem path segment. The JSON path joins no path, so it looks an
            unsafe id up as an ordinary key and answers ``unknown``/``absent`` like
            any other unrecognised id, never raising.
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
    """
    if source is None:
        return lambda _ticket_id: RunState.unknown
    if source.kind == "json":
        parsed = read_json_run_state(source.path)

        def resolve_json(ticket_id: str) -> RunState:
            if ticket_id in parsed.states:
                return parsed.states[ticket_id]
            if not parsed.readable or ticket_id in parsed.known_ticket_ids:
                return RunState.unknown
            return RunState.absent

        return resolve_json
    return lambda ticket_id: probe_ticket_state(source.path, ticket_id)
