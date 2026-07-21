"""In-memory :class:`FakeFileAdapter` — a side-effect-free FileAdapter for tests.

The fake is seeded with a :class:`Project`, a list of :class:`Ticket` s, an
optional ``{ticket_id: RunState}`` map, and an optional :class:`Roadmap`, then
answers every :class:`~factory_console.file_adapter.protocol.FileAdapter` method
as a pure read over that in-memory data — no filesystem access, no I/O, and no
mutation of the seeded values.

Two derived semantics are pinned here (and covered by the unit tests) so the
list view and the dependency view can never disagree:

* ``depCount`` is ``len(ticket.dependsOn)`` — the count of ALL declared direct
  dependencies, including ids that do not resolve to a seeded ticket (a dangling
  edge still counts as a declared dependency).
* the reverse index (``dependentCount`` and ``directDependents``) counts only
  OTHER seeded tickets that name the id in their ``dependsOn`` — a ticket is
  never its own dependent, so a self-referential edge does not inflate it (that
  edge does still count toward the ticket's own ``depCount``).
"""

from __future__ import annotations

from pathlib import Path

from factory_console.domain import (
    DepNeighborhood,
    Project,
    Roadmap,
    RunState,
    Ticket,
    TicketSummary,
)


class FakeFileAdapter:
    """In-memory :class:`FileAdapter` implementation for deterministic tests.

    Satisfies the read-only ``FileAdapter`` Protocol structurally (no
    inheritance needed); ``isinstance(fake, FileAdapter)`` holds because the
    Protocol is ``@runtime_checkable``.
    """

    def __init__(
        self,
        project: Project,
        tickets: list[Ticket],
        run_states: dict[str, RunState] | None = None,
        roadmap: Roadmap | None = None,
    ) -> None:
        """Seed the fake with pre-resolved project data.

        ``run_states`` is normalized to ``{}`` when ``None`` (every ticket then
        resolves to :attr:`RunState.unknown`). The seeded ``tickets`` order is
        preserved for every list-shaped result; a ``{id: Ticket}`` index gives
        O(1) lookup and a reverse ``{id: [dependent Ticket]}`` index (seeded
        order, self-edges excluded) backs ``dependentCount`` / ``directDependents``.
        """
        self._project = project
        self._tickets = tickets
        self._run_states = {} if run_states is None else run_states
        self._roadmap = roadmap
        self._by_id: dict[str, Ticket] = {ticket.id: ticket for ticket in tickets}
        self._dependents: dict[str, list[Ticket]] = {}
        for ticket in tickets:
            for dep_id in ticket.dependsOn:
                if dep_id == ticket.id:
                    continue  # a ticket is never its own dependent
                self._dependents.setdefault(dep_id, []).append(ticket)

    def _summarize(self, ticket: Ticket) -> TicketSummary:
        """Project a seeded :class:`Ticket` to its :class:`TicketSummary`.

        Single source of the projection, used by BOTH :meth:`list_tickets` and
        :meth:`get_deps`, so the two views cannot drift. ``depCount`` counts all
        declared deps (dangling included); ``dependentCount`` reads the reverse
        index (other seeded tickets that depend on this one).
        """
        return TicketSummary(
            id=ticket.id,
            title=ticket.title,
            status=ticket.status,
            track=ticket.track,
            milestone=ticket.milestone,
            runState=self._run_states.get(ticket.id, RunState.unknown),
            depCount=len(ticket.dependsOn),
            dependentCount=len(self._dependents.get(ticket.id, [])),
        )

    def load_project(self, root: Path) -> Project:
        """Return the seeded :class:`Project`.

        ``root`` is accepted to satisfy the Protocol but ignored: the in-memory
        fake is pre-seeded with its project and performs no discovery.
        """
        return self._project

    def list_tickets(self, project: Project) -> list[TicketSummary]:
        """Return a :class:`TicketSummary` for every seeded ticket, in seeded order."""
        return [self._summarize(ticket) for ticket in self._tickets]

    def get_ticket(self, project: Project, ticket_id: str) -> Ticket | None:
        """Return the seeded :class:`Ticket` for ``ticket_id``, or ``None`` if unseeded."""
        return self._by_id.get(ticket_id)

    def get_deps(self, project: Project, ticket_id: str) -> DepNeighborhood | None:
        """Return the :class:`DepNeighborhood` for ``ticket_id``, or ``None`` if unseeded.

        ``directDeps`` are the summaries of each ``dependsOn`` id that resolves to
        a seeded ticket (in ``dependsOn`` order); ``unresolvedDeps`` are the
        ``dependsOn`` ids with no seeded ticket (same order); ``directDependents``
        are the summaries of every OTHER seeded ticket that depends on
        ``ticket_id`` (in seeded order).
        """
        ticket = self._by_id.get(ticket_id)
        if ticket is None:
            return None
        return DepNeighborhood(
            ticket=self._summarize(ticket),
            directDeps=[
                self._summarize(self._by_id[dep_id])
                for dep_id in ticket.dependsOn
                if dep_id in self._by_id
            ],
            directDependents=[
                self._summarize(dependent) for dependent in self._dependents.get(ticket_id, [])
            ],
            unresolvedDeps=[dep_id for dep_id in ticket.dependsOn if dep_id not in self._by_id],
        )

    def read_run_state(self, project: Project, ticket_id: str) -> RunState:
        """Return the seeded run-state for ``ticket_id``, else :attr:`RunState.unknown`.

        Non-optional: an unseeded id (or a ticket with no seeded run-state) yields
        :attr:`RunState.unknown` rather than ``None``.
        """
        return self._run_states.get(ticket_id, RunState.unknown)

    def get_roadmap(self, project: Project) -> Roadmap | None:
        """Return the seeded :class:`Roadmap`, or ``None`` when seeded without one."""
        return self._roadmap
