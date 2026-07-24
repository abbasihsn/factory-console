# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.
"""Probe the factory run-state directory to resolve a ticket's :class:`RunState`.

The factory run-state directory (see ``ARCHITECTURE.md`` "Factory run-state
directory (read-only)") is authoritative for whether a ticket is mutable and
drives the ``RunState`` badge in the console. This module only *reads* it: it
locates the directory via the documented fallback order and maps a ticket's
on-disk marker (a file or subdirectory under a state name) to a ``RunState``
member. The console MUST NOT write, create, or delete anything here — a guard
test asserts this module contains no filesystem-mutating call.
"""

from __future__ import annotations

import re
from pathlib import Path

from factory_console.domain import TICKET_ID_PATTERN, RunState
from factory_console.file_adapter.path_safety import PathTraversal

# On-disk run-state directory names in precedence order, highest wins. These are
# the literal directory names under the run-state dir (``in-flight`` hyphenated);
# each is mapped to its enum member BY VALUE via ``RunState(name)``, never by
# string guessing. See ARCHITECTURE.md "Factory run-state directory (read-only)".
_MARKER_PRECEDENCE = ("merged", "ready", "in-flight", "todo")

# The documented run-state directory locations, project-relative, in fallback
# order (highest precedence first). Single source of truth for WHERE the
# run-state dir can live: :func:`find_run_state_dir` probes these under a project
# root, and the T40 ``RealFileWatcher`` derives its run-state scope prefixes from
# the same tuple so the prober and the watcher cannot drift. See ARCHITECTURE.md
# "Factory run-state directory (read-only)".
RUN_STATE_RELATIVE_LOCATIONS: tuple[Path, ...] = (
    Path(".factory") / "run-state",
    Path("docs") / "planning" / ".run-state",
)


def find_run_state_dir(project_root: Path) -> Path | None:
    """Return the project's run-state directory, or ``None`` if none is present.

    Probes the documented locations (:data:`RUN_STATE_RELATIVE_LOCATIONS`) in
    fallback order and returns the FIRST that is a directory:

    1. ``<project_root>/.factory/run-state``
    2. ``<project_root>/docs/planning/.run-state``

    Uses :meth:`Path.is_dir` (not :meth:`Path.exists`) because a plain file at
    that path is not a usable run-state directory — only a real directory can
    hold the per-state marker subdirectories.
    """
    for relative in RUN_STATE_RELATIVE_LOCATIONS:
        candidate = project_root / relative
        if candidate.is_dir():
            return candidate
    return None


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

    return RunState.todo
