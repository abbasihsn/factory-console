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
from collections.abc import Callable
from pathlib import Path

from factory_console.domain import RunState
from factory_console.domain.run_state_source import (
    RUN_STATE_SOURCE_LOCATIONS,
    JsonRunState,
    RunStateSource,
)

# Re-exported, not merely imported: this module's ``Raises:`` contract names
# ``PathTraversal``, and a test pins that the class callers catch from HERE is the
# same object ``path_safety`` defines — one unsafe-id exception, not one per module.
from factory_console.file_adapter.path_safety import PathTraversal as PathTraversal
from factory_console.file_adapter.path_safety import (
    is_contained,
    require_safe_ticket_id_segment,
)

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

    CONTAINMENT is checked too, and it must be checked HERE rather than at each
    read: ``is_file()``/``is_dir()`` follow symlinks, so a
    ``.factory/run-state.json`` symlinked outside the project root satisfies the
    node-type check, and every consumer of the returned source then reads it —
    :meth:`~factory_console.file_adapter.real.RealFileAdapter.list_tickets`,
    ``read_run_state``, and
    :func:`~factory_console.file_adapter.runs.read_pr_urls` alike. Checking it at
    ONE of those reads (as :func:`~factory_console.file_adapter.runs.find_run_state_path`
    does for the runs endpoint's ``sources`` report and its PR urls) makes the
    consumers DISAGREE: the endpoint would report ``runState`` as not found while
    serving ticket states parsed out of that same refused file, turning the
    console into a read oracle over out-of-root JSON. Resolving an escaping
    source to ``None`` degrades every consumer identically — no states, no PR
    urls, ``found: false``, and ``runState`` named in ``RunRecord.unavailable``
    — which is the honest answer for an artifact this console may not read. It is
    logged so the degradation is attributable rather than silent.

    A source that is skipped for containment does NOT fall through to a
    lower-precedence location: a project whose highest-precedence run-state
    escapes the root has an unreadable run-state, not a different one.
    """
    for kind, relative in RUN_STATE_SOURCE_LOCATIONS:
        candidate = project_root / relative
        present = candidate.is_file() if kind == "json" else candidate.is_dir()
        if not present:
            continue
        if not is_contained(candidate, project_root):
            _LOGGER.warning(
                "run-state: %s resolves outside the project root; treating it as absent",
                candidate,
            )
            return None
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
    - A present run-state directory with no matching marker -> :attr:`RunState.todo`
      (the "present-dir-but-missing-marker" default).

    Raises:
        PathTraversal: if ``ticket_id`` is not a single path-safe segment. This
            is raised BEFORE any filesystem lookup.
    """
    if run_state_dir is None:
        return RunState.unknown

    # Defense-in-depth: this id was already validated at the API boundary, but it
    # is about to be used as a filesystem path segment, so re-validate here —
    # through the ONE shared rule in ``path_safety``, per the ARCHITECTURE
    # run-state directory contract.
    require_safe_ticket_id_segment(ticket_id)

    for state in _MARKER_PRECEDENCE:
        # ``.exists()`` covers a marker present as either a file or a directory.
        if (run_state_dir / state / ticket_id).exists():
            return RunState(state)

    return RunState.todo


def read_json_run_state(path: Path) -> JsonRunState:
    """Parse the factory's ``run-state.json`` at ``path`` into a :class:`JsonRunState`.

    Reads ``.tickets`` — ``{TICKET_ID: {"status": str, "pr_url": str|null}}`` —
    and maps each ``status`` through :data:`FACTORY_STATUS_ALIASES`. A status the
    table does not name maps to :attr:`RunState.unknown` AND is collected into
    ``unrecognised`` (de-duplicated, first-seen order): a tenth factory state must
    be visible as a named gap, never silently dropped.

    Each entry's ``pr_url`` is collected into ``pr_urls`` when it is a non-empty
    string (the factory writes ``null`` when a ticket has no PR yet), so callers
    that need the url — the T81 runs endpoint — read it from THIS parse instead of
    opening ``run-state.json`` a second time with a second set of assumptions.

    NEVER raises. A run-state file that cannot be trusted is a source-level
    problem, not a request failure — "I could not tell" is the honest answer — so
    an unreadable file, unparseable JSON, a non-object document, an absent
    ``tickets`` key, and a ``tickets`` that is not an object (e.g. a list) all
    yield an EMPTY :class:`JsonRunState`, i.e. :attr:`RunState.unknown` for every
    ticket. Each case is logged at ``warning`` so the degradation leaves a trace.
    Individual entries that are not objects, or that carry a non-string
    ``status``, are skipped the same way without discarding the entries that are
    fine.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        _LOGGER.warning("run-state: %s could not be read; every ticket resolves unknown", path)
        return JsonRunState()
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
        return JsonRunState()

    tickets = document.get("tickets") if isinstance(document, dict) else None
    if not isinstance(tickets, dict):
        _LOGGER.warning(
            "run-state: %s has no 'tickets' object (found %s); every ticket resolves unknown",
            path,
            type(tickets).__name__,
        )
        return JsonRunState()

    states: dict[str, RunState] = {}
    unrecognised: list[str] = []
    pr_urls: dict[str, str] = {}
    for ticket_id, entry in tickets.items():
        status = entry.get("status") if isinstance(entry, dict) else None
        # Collected from the same entry as the status, before the status checks
        # below can ``continue`` past it: a ticket whose status this console does
        # not recognise still has a PR url worth surfacing. ``null`` (the
        # factory's "no PR yet") and any non-string are simply not recorded.
        if isinstance(entry, dict):
            pr_url = entry.get("pr_url")
            if isinstance(pr_url, str) and pr_url:
                pr_urls[ticket_id] = pr_url
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
    return JsonRunState(states=states, unrecognised=unrecognised, pr_urls=pr_urls)


def probe_ticket_state_from_source(source: RunStateSource | None, ticket_id: str) -> RunState:
    """Resolve ``ticket_id``'s :class:`RunState` from the project's run-state source.

    The source-aware entry point every caller should use; it dispatches on
    ``source.kind`` so a JSON-sourced project and a directory-sourced one resolve
    through the same call:

    - ``source is None`` -> :attr:`RunState.unknown`, unchanged from the
      directory-only behaviour.
    - ``kind == "json"`` -> the ticket's entry in the parsed file, else
      :attr:`RunState.unknown` (no entry, unrecognised status, or an unreadable
      or malformed file).
    - ``kind == "directory"`` -> :func:`probe_ticket_state`, unchanged.

    Resolving one ticket re-reads the source, matching ``ARCHITECTURE.md``
    "every request re-reads"; a caller resolving MANY tickets should take a
    :func:`run_state_resolver` instead, which reads the JSON once.

    Raises:
        PathTraversal: only on the directory path, where ``ticket_id`` becomes a
            filesystem path segment. The JSON path joins no path, so it looks an
            unsafe id up as an ordinary (absent) key and answers ``unknown``.
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
        states = read_json_run_state(source.path).states
        return lambda ticket_id: states.get(ticket_id, RunState.unknown)
    return lambda ticket_id: probe_ticket_state(source.path, ticket_id)
