"""The factory JSON artefacts this console both READS and WATCHES — one list.

The console reads several artefacts the factory writes under ``.factory/``, and it
streams a :class:`~factory_console.domain.watch.ChangeEvent` when one of them
changes so the SPA can refresh. Those two facts were kept in two places, and they
drifted twice: T91 found ``.factory/run-state.json`` read but unwatched, fixed it
for that ONE file, and the very next audit found ``.factory/metrics/ledger.jsonl``
in the same condition — read by ``GET /api/v1/spend`` since T79, scheduled by
nobody, so an append fired no event and live spend was silently dead (T95).

T99 is the third instance and the first this list absorbed rather than discovered:
``.factory/results/<ticket_id>.json``, ``.factory/receipts/<ticket_id>.json`` and
``.factory/last-stop.json`` have been read by ``GET /api/v1/runs`` since T88/T90 and
scheduled by nobody, so ``/runs`` — the one view whose whole content is those files —
could never live-update. Adding them here is the entire fix on the watcher's side.

All were omissions, not mistakes: nothing connected "a reader learned a path" to
"the watcher learned a path", so the second was only ever a convention away.
:data:`WATCHED_JSON_ARTIFACTS` is that connection. It is the ONE list the
watcher schedules from, and the one every JSON-artefact reader takes its path
constant from — ``file_adapter/ledger.py`` imports :data:`LEDGER_RELATIVE_PATH`
from here and ``file_adapter/runs.py`` imports its three, and the run-state half is
derived from
:data:`~factory_console.domain.run_state_source.RUN_STATE_SOURCE_LOCATIONS`,
which the prober already owns. Adding an artefact to a reader without adding it
to the watcher therefore is not something a future editor has to REMEMBER not to
do; there is no second place to add it to.

It lives in ``domain`` for the reason
:data:`~factory_console.domain.run_state_source.RUN_STATE_SOURCE_LOCATIONS`
does: ``file_adapter`` depends on ``domain`` and never the reverse, so a constant
two adapters share cannot live in either of them.

Scope: the JSON/JSONL artefacts, in the two shapes the factory writes them in. A
``"file"`` entry is ONE file at a fixed name, which the watcher reaches by
scheduling its parent directory non-recursively and filtering by name. A ``"dir"``
entry is a directory of per-ticket files whose names are not known in advance
(``.factory/results/<ticket_id>.json``), which the watcher reaches by scheduling
that directory itself non-recursively and taking any file directly inside it. The
run-state DIRECTORY locations are neither: they are a recursive watch on a whole
subtree and stay with
:data:`~factory_console.file_adapter.run_state.RUN_STATE_RELATIVE_LOCATIONS`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from factory_console.domain.run_state_source import RUN_STATE_SOURCE_LOCATIONS
from factory_console.domain.watch import ChangeScope

ArtifactKind = Literal["file", "dir"]
"""How a :data:`WATCHED_JSON_ARTIFACTS` entry's path is MATCHED against an event.

``"file"`` — the path names one file at a fixed name; an event matches when its
relative path IS that path. ``"dir"`` — the path names a directory whose per-ticket
filenames are dynamic; an event matches when it is a FILE whose immediate parent is
that directory. The discriminator is carried rather than inferred from the path
shape (a trailing separator, an existence check) because the answer must not depend
on what happens to be on disk when the watcher starts: ``.factory/`` is gitignored,
so every one of these paths is routinely absent on a fresh clone.
"""

LEDGER_RELATIVE_PATH: Path = Path(".factory") / "metrics" / "ledger.jsonl"
"""The spend ledger's project-relative location (ARCHITECTURE.md "Other factory
artefacts (read-only)").

Single source of truth for WHERE the ledger lives:
:func:`~factory_console.file_adapter.ledger.find_ledger_path` probes exactly this
under a project root, and the watcher schedules exactly this file's parent. It sits
here rather than in the reader so those two cannot be separate literals for the same
path — which is precisely how the file came to be read and never watched.
"""

RESULTS_RELATIVE_DIR: Path = Path(".factory") / "results"
"""The lane results' project-relative DIRECTORY, holding ``<ticket_id>.json`` per lane."""

RECEIPTS_RELATIVE_DIR: Path = Path(".factory") / "receipts"
"""The review receipts' project-relative DIRECTORY, holding ``<ticket_id>.json`` per lane."""

LAST_STOP_RELATIVE_PATH: Path = Path(".factory") / "last-stop.json"
"""Why the last run stopped — one file per project, at a fixed name.

These three are the ``GET /api/v1/runs`` sources (ARCHITECTURE.md "Other factory
artefacts (read-only)"), and they live here for the reason
:data:`LEDGER_RELATIVE_PATH` does: :mod:`~factory_console.file_adapter.runs` probes
exactly these under a project root and the watcher schedules exactly these, so the
reader and the watcher cannot hold two literals for one path — which is precisely
how all three came to be read and never watched (T99).
"""

WATCHED_JSON_ARTIFACTS: tuple[tuple[ChangeScope, Path, ArtifactKind], ...] = (
    *(
        ("run-state", relative, "file")
        for kind, relative in RUN_STATE_SOURCE_LOCATIONS
        if kind == "json"
    ),
    ("ledger", LEDGER_RELATIVE_PATH, "file"),
    ("runs", RESULTS_RELATIVE_DIR, "dir"),
    ("runs", RECEIPTS_RELATIVE_DIR, "dir"),
    ("runs", LAST_STOP_RELATIVE_PATH, "file"),
)
"""Every factory JSON artefact the watcher observes: scope, path, and how to match it.

The scope is carried alongside the path — rather than re-derived from the path shape
in the handler — so the answer to "what does a change here mean?" is stated once, at
the same place the path is stated. The :data:`ArtifactKind` is carried for the same
reason, and it is what lets ONE list cover both shapes the factory writes in:

- ``"file"`` (``run-state.json``, the ledger, ``last-stop.json``) — the watcher
  schedules the entry's PARENT directory non-recursively and the handler matches the
  exact relative path. The parent, not the file: watchdog schedules directories, and
  the factory replaces these files via ``mktemp`` + ``mv`` (INV-03), so a watch bound
  to the file's inode goes quiet after the first update.
- ``"dir"`` (results, receipts) — the watcher schedules the entry's OWN path
  non-recursively and the handler matches any FILE directly inside it. A lane's
  artefact lands at ``<ticket_id>.json``, a name no constant can spell in advance, so
  an exact-path match cannot express it; the directory IS the watched thing. INV-03
  applies here too and is answered the same way — the watch is on the directory, and
  names, never inodes, are what get matched.

Order is not significant — every entry is scheduled and matched independently — but
the run-state locations come first because they are derived from a tuple that IS
ordered (its probe precedence).
"""
