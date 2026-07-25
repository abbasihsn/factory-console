"""The single write-path chokepoint: is a ticket mutable in its run-state?

The core v2 safety invariant is that a ticket may be edited ONLY when its factory
run-state is ``todo`` (or ``unknown``, when no run-state directory is present);
``in-flight``/``ready``/``merged`` are read-only, matching how
``/factory-reconcile-plan`` treats them (see ``ARCHITECTURE.md`` "Factory
run-state directory (read-only)"). :func:`ensure_mutable` is the one gate every
mutating write passes before touching disk, and :class:`TicketNotMutable` is the
ONE canonical error for the non-todo condition across the whole write path.

This module REUSES the read-only prober
(:func:`~factory_console.file_adapter.run_state.probe_ticket_state`) to resolve
the state — it never re-implements run-state detection and never writes to the
run-state directory (it does no filesystem I/O of its own at all).
"""

from __future__ import annotations

from factory_console.domain import Project, RunState
from factory_console.errors import FactoryConsoleError
from factory_console.file_adapter.run_state import probe_ticket_state

# The ONLY editable predicate: a ticket is mutable exactly when its resolved
# run-state is ``todo`` or ``unknown`` (no run-state directory on disk). Every
# other state (``in-flight``/``ready``/``merged``) is read-only. Single source of
# truth for the write-authorization decision — see ARCHITECTURE.md "Factory
# run-state directory (read-only)".
MUTABLE_STATES = (RunState.todo, RunState.unknown)


class TicketNotMutable(FactoryConsoleError):
    """A ticket cannot be edited because its factory run-state is not mutable.

    Raised by :func:`ensure_mutable` when a ticket's probed :class:`RunState` is
    outside :data:`MUTABLE_STATES` (i.e. ``in-flight``/``ready``/``merged``). This
    is the single canonical write-path error for the non-todo condition; mapped to
    HTTP 409 (the edit conflicts with the ticket's current lifecycle state).
    ``details`` echoes the (user-supplied) ``ticketId`` and the resolved
    ``runState`` — both already-known values, never a resolved filesystem path.
    """

    def __init__(self, ticket_id: str, run_state: RunState) -> None:
        super().__init__(
            code="ticket_not_mutable",
            message=(f"Ticket {ticket_id} is not editable in run-state '{run_state.value}'"),
            status=409,
            details={"ticketId": ticket_id, "runState": run_state.value},
        )


def ensure_mutable(project: Project, ticket_id: str) -> RunState:
    """Return ``ticket_id``'s :class:`RunState` iff the ticket is editable.

    Resolves the state via the read-only prober
    (:func:`~factory_console.file_adapter.run_state.probe_ticket_state` over
    ``project.runStateDir``) and enforces the write invariant: a ``todo``/
    ``unknown`` ticket is mutable and its state is returned; any other state is
    read-only.

    Raises:
        TicketNotMutable: if the resolved state is not in :data:`MUTABLE_STATES`
            (``in-flight``/``ready``/``merged``) — HTTP 409.
        PathTraversal: if ``ticket_id`` is not a single path-safe segment AND the
            project has a run-state directory. It propagates UNCHANGED from the
            prober, which validates the id only on the path it actually probes:
            when ``project.runStateDir is None`` the prober short-circuits to the
            mutable ``unknown`` BEFORE the id is checked, so no ``PathTraversal``
            is raised. This gate authorizes by run-state and does no path I/O of
            its own, so it is NOT a path-safety guarantee: a downstream writer that
            turns ``ticket_id`` into a filesystem path MUST re-validate it at the
            point of use (as ``ticket_md``/``run_state`` do), never rely on this
            gate for path safety.
    """
    run_state = probe_ticket_state(project.runStateDir, ticket_id)
    if run_state not in MUTABLE_STATES:
        raise TicketNotMutable(ticket_id, run_state)
    return run_state
