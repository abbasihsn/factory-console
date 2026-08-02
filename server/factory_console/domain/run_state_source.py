"""WHICH run-state artifact a project's states were resolved from.

Two on-disk forms exist and both are read. The factory writes ONE JSON file,
``.factory/run-state.json``; the older console contract (``ARCHITECTURE.md``
"Factory run-state directory (read-only)") is a directory of per-state marker
subdirectories. Which one a project actually has is not a guess a caller should
have to re-derive from a path shape, so the prober reports it: a
:class:`RunStateSource` carries the resolved ``kind`` alongside its ``path``, and
every downstream read dispatches on that ``kind``.

:class:`JsonRunState` is the parsed result of the JSON form, and carries FOUR
things: the states it named; the statuses it named that this console does not
recognise (so a factory that gains a tenth status shows up as an explicit gap
rather than as a project silently full of ``unknown``); ``known_ticket_ids``,
every id the file mentioned at all, which is what separates "listed, but we could
not classify it" from "not listed" and so decides
:attr:`~factory_console.domain.run_state.RunState.absent` versus ``unknown``; and
``readable``, whether the file could be trusted at all. The last two are read
TOGETHER — an empty ``known_ticket_ids`` means "the file lists nobody" only when
``readable`` is true — because a file that could not be parsed must never resolve
``absent`` for every ticket and lock the project read-only (T80).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from factory_console.domain.run_state import RunState

RunStateSourceKind = Literal["json", "directory"]
"""The run-state artifact forms this console can read."""


class RunStateSource(BaseModel):
    """A resolved run-state artifact: its form (``kind``) and where it lives.

    Frozen and ``extra="forbid"`` like every other domain model, and carried on
    :class:`~factory_console.domain.project.Project` so the whole request reads
    through ONE resolution rather than each call site re-probing the filesystem.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RunStateSourceKind
    path: Path


class JsonRunState(BaseModel):
    """The tickets of one parsed ``.factory/run-state.json``.

    ``states`` maps ticket id -> :class:`RunState` for every entry whose factory
    status is in the alias table; ``unrecognised`` collects, de-duplicated and in
    first-seen order, the raw status strings that were NOT (their tickets map to
    :attr:`RunState.unknown`). ``known_ticket_ids`` names every id that had SOME
    entry in the document's ``tickets`` object, whether or not that entry's
    status was usable — it is what lets a caller tell "this id has an entry with
    a status we can't classify" (:attr:`RunState.unknown`) apart from "this id
    has no entry at all" (:attr:`RunState.absent`), a distinction ``states``
    alone cannot make since both cases are absent from it.

    ``readable`` is ``False`` for a file that could not be parsed at all — or
    whose ``tickets`` key is missing or is not an object. That degraded case
    stays ``unknown`` for every ticket queried (not ``absent``): a run-state file
    the console cannot trust is a source-level problem ("I could not tell"), not
    grounds to refuse edits to every ticket in the project.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    states: dict[str, RunState] = {}
    unrecognised: list[str] = []
    known_ticket_ids: frozenset[str] = frozenset()
    readable: bool = True


# The run-state artifact locations, project-relative, in probe order (highest
# precedence first) paired with the form expected at each. The JSON file wins
# because it is what the factory writes TODAY; the directory locations stay
# because nothing shows they are unused elsewhere. Single source of truth for
# WHERE run-state can live and WHAT node type is acceptable there:
# :func:`~factory_console.file_adapter.run_state.find_run_state_source` probes
# exactly this tuple.
RUN_STATE_SOURCE_LOCATIONS: tuple[tuple[RunStateSourceKind, Path], ...] = (
    ("json", Path(".factory") / "run-state.json"),
    ("directory", Path(".factory") / "run-state"),
    ("directory", Path("docs") / "planning" / ".run-state"),
)
