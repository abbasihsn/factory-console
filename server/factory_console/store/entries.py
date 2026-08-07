"""The registry/condition join: registry rows in, one :class:`RegistryEntry` out per row.

T109 gave the console a way to say what a single registered path currently IS
(:class:`~factory_console.file_adapter.project_condition.ProjectConditionProbe`),
and the registry can list the rows the user asked to track. Putting the two
together is one small pure fold, and it gets its own module because the fold
carries an invariant worth stating, naming and testing on its own:

    **len(result) == len(projects), ALWAYS.**

A degraded row is TRANSFORMED — carried through as a
:class:`~factory_console.domain.registry.RegistryEntry` whose ``condition`` names
the degradation — and NEVER filtered. That is not pedantry about list lengths. The
entire reason :data:`~factory_console.domain.registry.RegistryEntryCondition` has
five members instead of being a boolean is that a registered project whose path has
gone away must still APPEAR in the listing: a row silently dropped reads to the user
as "I never registered that", which is a false statement the console would be making
about the user's own past action. This is exactly the failure ``ARCHITECTURE.md``'s
"The resolution invariant" names — what could not be established is **recorded,
never dropped**, because "discarding an entry that failed to ``stat`` leaves a
collection that looks smaller and cleaner, which reads downstream as *more*
information, not less". Filtering is the tempting implementation precisely because
its output looks tidier; tidier here means lying.

**Why the join lives here and not inside a handler.** Written as a loop inside the
listing endpoint, the ordering rule and the never-drop rule would be untestable
except through HTTP, and would have to be re-established by every future caller (the
CLI, a second endpoint, an export). As a module-level function they are ONE testable
unit with a regression test pinned to the invariant — the same reason ``RunService``
composes ``/runs`` rather than the router assembling it inline.

**Why it lives in ``store/`` and not ``file_adapter/``.** The fold's input is the
console's OWN rows, and it performs no I/O whatsoever: every filesystem answer
arrives already-made through the injected probe, which is itself a ``file_adapter``
port precisely because reading a target project's files is that layer's job. So this
module never reaches across the ownership line it sits beside — it composes what the
two layers each produced (``PROJECT_STRUCTURE.md``, track ownership).

**Synchronicity is part of the contract.** :func:`resolve_entries` is SYNCHRONOUS
and blocking, because the injected probe does blocking ``stat`` work. The caller —
the backend's registry listing, T112 — offloads the registry query and this fold
TOGETHER in a single ``anyio.to_thread.run_sync`` hop for the whole listing, rather
than one hop per row: a per-row hop would pay thread-pool hand-off cost N times for
work that is a handful of syscalls each, and would serialise no better. That caller
is not implemented here; this module only states the shape it must be called in
(``ARCHITECTURE.md``, Cross-cutting → Concurrency).

Like :mod:`~factory_console.file_adapter.project_condition` and
:mod:`~factory_console.file_adapter.run_artifacts`, this module is deliberately NOT
re-exported from its package ``__init__``; consumers import :func:`resolve_entries`
by full path, so adding it touches no aggregation file.
"""

from __future__ import annotations

from collections.abc import Iterable

from factory_console.domain.registry import RegisteredProject, RegistryEntry
from factory_console.file_adapter.project_condition import ProjectConditionProbe


def resolve_entries(
    projects: Iterable[RegisteredProject], probe: ProjectConditionProbe
) -> list[RegistryEntry]:
    """Join each registry row with its current condition, preserving count and order.

    Calls ``probe.probe`` exactly ONCE per row, in input order, and pairs each answer
    with the row it was asked about. It does no I/O of its own and holds no state: the
    probe owns every filesystem touch, which is what lets a caller exercise this fold
    against paths that exist on no disk.

    ``projects`` is consumed once and may be any iterable (the store hands over a
    cursor-backed sequence); the result is always a concrete ``list``, in the same
    order, of the same length. **No row is ever filtered out** — see the module
    docstring for why a dropped degraded row is a lie about the user's own
    registration, and ``ARCHITECTURE.md``'s "The resolution invariant" for the
    recorded-never-dropped rule it corollaries.

    No error handling appears here ON PURPOSE, and its absence is load-bearing rather
    than an omission: ``ProjectConditionProbe`` is TOTAL — a conforming implementation
    answers every source-level problem with a named condition and never raises — so a
    ``try`` around the call would be a defensive branch for a case the port forbids,
    and swallowing into a fabricated condition would report a probe BUG as a fact
    about the user's disk. A raise from a probe is a broken implementation and must
    surface as one.

    SYNCHRONOUS and blocking by design; the caller offloads the whole listing in one
    ``anyio.to_thread.run_sync`` hop (T112), never one hop per row.
    """
    return [
        RegistryEntry(project=project, condition=probe.probe(project.path)) for project in projects
    ]
