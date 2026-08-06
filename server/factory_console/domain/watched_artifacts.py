"""The factory JSON artefacts this console both READS and WATCHES — one list.

The console reads several artefacts the factory writes under ``.factory/``, and it
streams a :class:`~factory_console.domain.watch.ChangeEvent` when one of them
changes so the SPA can refresh. Those two facts were kept in two places, and they
drifted twice: T91 found ``.factory/run-state.json`` read but unwatched, fixed it
for that ONE file, and the very next audit found ``.factory/metrics/ledger.jsonl``
in the same condition — read by ``GET /api/v1/spend`` since T79, scheduled by
nobody, so an append fired no event and live spend was silently dead (T95).

Both were omissions, not mistakes: nothing connected "a reader learned a path" to
"the watcher learned a path", so the second was only ever a convention away.
:data:`WATCHED_JSON_ARTIFACTS` is that connection. It is the ONE list the
watcher schedules from, and the one every JSON-artefact reader takes its path
constant from — ``file_adapter/ledger.py`` imports :data:`LEDGER_RELATIVE_PATH`
from here, and the run-state half is derived from
:data:`~factory_console.domain.run_state_source.RUN_STATE_SOURCE_LOCATIONS`,
which the prober already owns. Adding an artefact to a reader without adding it
to the watcher therefore is not something a future editor has to REMEMBER not to
do; there is no second place to add it to.

It lives in ``domain`` for the reason
:data:`~factory_console.domain.run_state_source.RUN_STATE_SOURCE_LOCATIONS`
does: ``file_adapter`` depends on ``domain`` and never the reverse, so a constant
two adapters share cannot live in either of them.

Scope: the JSON/JSONL FILE artefacts, which are the ones the watcher reaches by
scheduling their parent directory non-recursively and filtering by name. The
run-state DIRECTORY locations are a different mechanism (a recursive watch on the
subtree) and stay with
:data:`~factory_console.file_adapter.run_state.RUN_STATE_RELATIVE_LOCATIONS`.
"""

from __future__ import annotations

from pathlib import Path

from factory_console.domain.run_state_source import RUN_STATE_SOURCE_LOCATIONS
from factory_console.domain.watch import ChangeScope

LEDGER_RELATIVE_PATH: Path = Path(".factory") / "metrics" / "ledger.jsonl"
"""The spend ledger's project-relative location (ARCHITECTURE.md "Other factory
artefacts (read-only)").

Single source of truth for WHERE the ledger lives:
:func:`~factory_console.file_adapter.ledger.find_ledger_path` probes exactly this
under a project root, and the watcher schedules exactly this file's parent. It sits
here rather than in the reader so those two cannot be separate literals for the same
path — which is precisely how the file came to be read and never watched.
"""

WATCHED_JSON_ARTIFACTS: tuple[tuple[ChangeScope, Path], ...] = (
    *(("run-state", relative) for kind, relative in RUN_STATE_SOURCE_LOCATIONS if kind == "json"),
    ("ledger", LEDGER_RELATIVE_PATH),
)
"""Every factory FILE artefact the watcher observes, paired with its ``ChangeScope``.

The scope is carried alongside the path — rather than re-derived from the path shape
in the handler — so the answer to "what does a change here mean?" is stated once, at
the same place the path is stated. The watcher builds its ``rel_path -> ChangeScope``
map from this tuple and schedules each entry's PARENT directory non-recursively,
because watchdog schedules directories and the factory replaces these files via
``mktemp`` + ``mv`` (INV-03): a watch bound to the file's inode goes quiet after the
first update.

Order is not significant — every entry is scheduled and every entry is matched by its
exact relative path — but the run-state locations come first because they are derived
from a tuple that IS ordered (its probe precedence).
"""
