"""The single write-path chokepoint: is a ticket mutable in its run-state?

The core v2 safety invariant is that a ticket may be edited ONLY when its factory
run-state is ``todo`` (or ``unknown``, when no run-state source is present);
every other state — ``in-flight``/``ready``/``merged`` from the directory form,
``in_progress``/``in_part``/``in_submilestone``/``flagged``/``failed``/
``needs_human`` from the factory's ``run-state.json``, and ``absent`` (a run-state
source resolved and does not list the ticket at all) — is read-only, matching
how ``/factory-reconcile-plan`` treats them (see ``ARCHITECTURE.md`` "Factory
run-state directory (read-only)"). :func:`ensure_mutable` is the one gate every
mutating write passes before touching disk, and :class:`TicketNotMutable` is the
ONE canonical error for the non-todo condition across the whole write path.

This module REUSES the read-only, source-aware prober
(:func:`~factory_console.file_adapter.run_state.probe_ticket_state_from_source`)
to resolve the state — it never re-implements run-state detection and never
writes to the run-state directory (it does no filesystem I/O of its own at all).
It reads through ``project.runStateSource``, not ``project.runStateDir``: a
JSON-sourced project has no run-state directory, and gating on the directory
there would see ``unknown`` for every ticket and wave through edits to tickets
the factory has already merged.
"""

from __future__ import annotations

from pathlib import Path

from factory_console.domain import Project, RunState
from factory_console.errors import FactoryConsoleError
from factory_console.file_adapter.run_state import probe_ticket_state_from_source

# The ONLY editable predicate: a ticket is mutable exactly when its resolved
# run-state is ``todo`` or ``unknown`` (no run-state source on disk). Every
# other state is read-only. Single source of truth for the write-authorization
# decision — see ARCHITECTURE.md "Factory run-state directory (read-only)".
MUTABLE_STATES = (RunState.todo, RunState.unknown)


class TicketNotMutable(FactoryConsoleError):
    """A ticket cannot be edited because its factory run-state is not mutable.

    Raised by :func:`ensure_mutable` when a ticket's resolved :class:`RunState`
    is outside :data:`MUTABLE_STATES` (any state but ``todo``/``unknown``). This
    is the single canonical write-path error for the non-todo condition; mapped to
    HTTP 409 (the edit conflicts with the ticket's current lifecycle state).
    ``details`` echoes the (user-supplied) ``ticketId`` and the resolved
    ``runState`` — both already-known values, never a resolved filesystem path.
    That rule is scoped to ``details`` ALONE, and deliberately so since T80: the
    ``absent`` ``message`` below does carry a resolved path, and both fields ship in
    the same client-facing envelope (:func:`~factory_console.errors.to_error_response`).
    Read it as "``details`` stays a stable, machine-readable pair", NOT as "this
    error never discloses a path" — the path is already public on this API via
    ``GET /api/v1/project``'s ``runStateSource.path``.

    ``source_path`` is optional and used ONLY to phrase a distinct message for
    :attr:`RunState.absent`: unlike the other read-only states (which name a real
    lifecycle a factory lane put the ticket in), ``absent`` means the resolved
    run-state source was consulted and simply does not mention this ticket — an
    operator seeing the refusal needs to know WHICH file was consulted, since the
    answer is "the file you are not looking at" (T80 step 4 mandates naming it).
    Every other state keeps the generic message; ``details`` is identical in shape
    either way, so a client that switches on ``runState`` never has to parse prose.
    """

    def __init__(
        self, ticket_id: str, run_state: RunState, *, source_path: Path | None = None
    ) -> None:
        if run_state is RunState.absent and source_path is not None:
            message = (
                f"Ticket {ticket_id} is not known to the run-state at {source_path}, "
                "so the console will not write it"
            )
        else:
            message = f"Ticket {ticket_id} is not editable in run-state '{run_state.value}'"
        super().__init__(
            code="ticket_not_mutable",
            message=message,
            status=409,
            details={"ticketId": ticket_id, "runState": run_state.value},
        )


def ensure_mutable(project: Project, ticket_id: str) -> RunState:
    """Return ``ticket_id``'s :class:`RunState` iff the ticket is editable.

    Resolves the state via the read-only, source-aware prober
    (:func:`~factory_console.file_adapter.run_state.probe_ticket_state_from_source`
    over ``project.runStateSource``) and enforces the write invariant: a ``todo``/
    ``unknown`` ticket is mutable and its state is returned; any other state is
    read-only.

    Raises:
        TicketNotMutable: if the resolved state is not in :data:`MUTABLE_STATES`
            — HTTP 409.
        PathTraversal: if ``ticket_id`` is not a single path-safe segment AND the
            project's source is a run-state DIRECTORY. It propagates UNCHANGED
            from the prober, which validates the id only on the path it actually
            probes: with no source (or a JSON source, which joins no path) the
            prober answers ``unknown``/no-entry BEFORE the id is checked as a path
            segment, so no ``PathTraversal`` is raised. This gate authorizes by
            run-state and does no path I/O of
            its own, so it is NOT a path-safety guarantee: a downstream writer that
            turns ``ticket_id`` into a filesystem path MUST re-validate it at the
            point of use (as ``ticket_md``/``run_state`` do), never rely on this
            gate for path safety.
    """
    run_state = probe_ticket_state_from_source(project.runStateSource, ticket_id)
    if run_state not in MUTABLE_STATES:
        source_path = project.runStateSource.path if project.runStateSource else None
        raise TicketNotMutable(ticket_id, run_state, source_path=source_path)
    return run_state
