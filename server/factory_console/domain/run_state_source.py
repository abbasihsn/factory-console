"""WHICH run-state artifact a project's states were resolved from.

Two on-disk forms exist and both are read. The factory writes ONE JSON file,
``.factory/run-state.json``; the older console contract (``ARCHITECTURE.md``
"Factory run-state directory (read-only)") is a directory of per-state marker
subdirectories. Which one a project actually has is not a guess a caller should
have to re-derive from a path shape, so the prober reports it: a
:class:`RunStateSource` carries the resolved ``kind`` alongside its ``path``, and
every downstream read dispatches on that ``kind``.

:class:`JsonRunState` is the parsed result of the JSON form — the states it
named, plus the statuses it named that this console does not recognise. The
second list exists so a factory that gains a tenth status shows up as an
explicit gap rather than as a project silently full of ``unknown``.
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

    @property
    def carriesPrUrls(self) -> bool:
        """True if this artifact form can supply PR urls at all.

        Only the factory's JSON file does; the legacy marker directory records a
        state per ticket and nothing else. Owned HERE, on the source, because it
        is a fact about the source's own ``kind`` — and because two call sites
        must never disagree about it:
        :func:`~factory_console.file_adapter.runs.read_pr_urls` uses it to decide
        whether to return any urls, and
        :meth:`~factory_console.services.run_service.RunService._compose` uses it
        to decide whether ``runState`` is named in ``RunRecord.unavailable``. If
        those two ever drifted, a ``prUrl`` would go null with no source named —
        the unattributable null the record exists to prevent.
        """
        return self.kind == "json"


class JsonRunState(BaseModel):
    """The tickets of one parsed ``.factory/run-state.json``.

    ``states`` maps ticket id -> :class:`RunState` for every entry whose factory
    status is in the alias table; ``unrecognised`` collects, de-duplicated and in
    first-seen order, the raw status strings that were NOT (their tickets map to
    :attr:`RunState.unknown`). A file that could not be parsed at all — or whose
    ``tickets`` key is missing or is not an object — yields empty ``states``, so
    every ticket resolves ``unknown`` without the read failing.

    ``pr_urls`` maps ticket id -> PR url for the entries that carry a non-empty
    string ``pr_url`` (the factory writes ``null`` for a ticket with no PR, and a
    ticket with no url is simply absent from the map rather than present as
    ``None``). It is read here, alongside ``status``, because both live in the
    same entry of the same file: the T81 runs endpoint needs the url, and having
    it re-open ``run-state.json`` would make a second parser of the factory's
    format, free to drift from this one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    states: dict[str, RunState] = {}
    unrecognised: list[str] = []
    pr_urls: dict[str, str] = {}


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
