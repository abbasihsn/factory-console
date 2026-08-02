"""WHICH run-state artifact a project's states were resolved from.

Two on-disk forms exist and both are read. The factory writes ONE JSON file,
``.factory/run-state.json``; the older console contract (``ARCHITECTURE.md``
"Factory run-state directory (read-only)") is a directory of per-state marker
subdirectories. Which one a project actually has is not a guess a caller should
have to re-derive from a path shape, so the prober reports it: a
:class:`RunStateSource` carries the resolved ``kind`` alongside its ``path``, and
every downstream read dispatches on that ``kind``.

:class:`JsonRunState` is the parsed result of the JSON form, and carries FIVE
things: the states it named; the statuses it named that this console does not
recognise (so a factory that gains a tenth status shows up as an explicit gap
rather than as a project silently full of ``unknown``); ``known_ticket_ids``,
every id the file mentioned at all, which is what separates "listed, but we could
not classify it" from "not listed" and so decides
:attr:`~factory_console.domain.run_state.RunState.absent` versus ``unknown``;
``unclassifiable``, per id, WHAT the file said that could not be classified (T80
amendment 4); and ``readable``,
whether the file could be trusted at all. ``readable`` and ``known_ticket_ids`` are
read TOGETHER — an empty ``known_ticket_ids`` means "the file lists nobody" only
when ``readable`` is true — because a file that could not be parsed must never
resolve ``absent`` for every ticket and lock the project read-only (T80). A SIXTH
flag, ``unreadable``, narrows the ``readable=False`` case to the one that must fail
CLOSED: the file's bytes could not be read at all (T80 amendment 2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

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
    first-seen order, the raw status strings that were NOT (their tickets resolve the
    refusing :attr:`RunState.unreadable` — T80 amendment 4; they used to resolve the
    mutable ``unknown``). ``known_ticket_ids`` names every id that had SOME
    entry in the document's ``tickets`` object, whether or not that entry's
    status was usable — it is what lets a caller tell "this id has an entry with
    a status we can't classify" (:attr:`RunState.unreadable` since T80 amendment 4)
    apart from "this id has no entry at all" (:attr:`RunState.absent`), a
    distinction ``states`` alone cannot make since both cases are absent from it.

    ``unclassifiable`` names the FIRST of those two per id: for every id in
    ``known_ticket_ids`` that is not in ``states``, a short description of WHAT the file
    said about it that could not be classified — ``"status 'in_review'"``, ``"an entry
    with no status"``, ``"an entry that is not an object"``. Like ``unrecognised`` it is
    a parse artifact, not a verdict: it records what was read, and says nothing about
    what any gate should do with it.

    It is a description rather than the raw value because the value is not always a
    string, so there is no raw form to carry: a schema drift to ``{"T42": "merged"}``
    puts a ``str`` where the object belongs, and ``{"status": 7}`` puts an ``int`` where
    the status does. It is keyed per id — rather than left to ``unrecognised``, which is
    de-duplicated across the whole file — because ``unrecognised`` cannot say which
    value belonged to which ticket, and "which ticket" is the question a caller
    resolving ONE id is asking. The two are both required and neither replaces the
    other: ``unrecognised`` describes the file, ``unclassifiable`` describes an entry.

    ``readable`` is ``False`` for a file that could not be parsed at all — or
    whose ``tickets`` key is missing or is not an object. That degraded case
    stays ``unknown`` for every ticket queried (not ``absent``): a run-state file
    the console cannot trust is a source-level problem ("I could not tell"), not
    grounds to refuse edits to every ticket in the project.

    ``unreadable`` splits that degraded case in two, and only the FIRST of them
    fails closed. It is ``True`` only when the file's BYTES could not be read (an
    ``OSError`` that is not "the file is not there" — ``EACCES``, ``EIO``,
    ``EISDIR``), which resolves :attr:`RunState.unreadable` for every ticket and is
    refused by both write gates: a source that exists and will not be read may be
    hiding a ``merged`` entry, and granting a write because a permission error
    prevented the check is the one direction this console does not fail. It stays
    ``False`` for a file that VANISHED (``ENOENT``/``ENOTDIR`` — nothing is there to
    be unreadable, so it is indistinguishable from having no source) and for one
    whose bytes WERE read and made no sense (non-UTF-8, invalid JSON, no ``tickets``
    object): those are content problems and keep the mutable ``unknown``.
    ``unreadable=True`` always implies ``readable=False``; the reverse does not hold.
    That implication is ENFORCED (see :meth:`_reject_readable_and_unreadable`), not
    merely documented: the two flags describe three outcomes, not four, and the fourth
    combination is the one a resolver would read inconsistently depending on which flag
    it happened to check first.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    states: dict[str, RunState] = {}
    unrecognised: list[str] = []
    known_ticket_ids: frozenset[str] = frozenset()
    unclassifiable: dict[str, str] = {}
    readable: bool = True
    unreadable: bool = False

    @model_validator(mode="after")
    def _reject_readable_and_unreadable(self) -> JsonRunState:
        """Make the impossible fourth combination unrepresentable rather than documented.

        ``readable=True, unreadable=True`` claims the file was both trusted and never
        read. No parse path produces it, and every consumer would have to pick a flag to
        believe: ``_resolve_json_state`` checks ``unreadable`` first and would refuse a
        file it was simultaneously told to trust, while a consumer checking ``readable``
        first would grant writes against bytes nobody read — the exact fail-open T80
        amendment 2 closes. Rejecting it at construction keeps that choice from ever
        being load-bearing.
        """
        if self.readable and self.unreadable:
            raise ValueError(
                "JsonRunState(readable=True, unreadable=True) is not a state a run-state "
                "file can be in: unreadable bytes cannot also have parsed"
            )
        return self


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
