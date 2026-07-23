"""Shared ticket projection — the one place list-view and dep-view summaries are built.

Both production :class:`~factory_console.file_adapter.protocol.FileAdapter`
implementations — the filesystem-backed
:class:`~factory_console.file_adapter.real.RealFileAdapter` and the in-memory
:class:`~factory_console.file_adapter.fake.FakeFileAdapter` — project the same
ticket list to :class:`~factory_console.domain.TicketSummary` /
:class:`~factory_console.domain.DepNeighborhood` with identical derived
semantics; they differ ONLY in where a ticket's run-state comes from (an on-disk
probe vs. a seeded map). Centralizing the projection here — with the run-state
source injected as ``run_state_for`` — means the two adapters cannot silently
drift: the list view and the dependency view share ONE code path in BOTH.

The two derived semantics pinned here (and covered by the adapter tests):

* ``depCount`` is ``len(ticket.dependsOn)`` — the count of ALL declared direct
  dependencies, dangling ids included.
* the reverse index (``dependentCount`` / ``directDependents``) counts only
  OTHER tickets that name the id, so a self-referential edge never inflates it
  (that edge still counts toward the ticket's own ``depCount``).
* the neighborhood's ``directDeps`` / ``unresolvedDeps`` name each neighbour ONCE
  (first-seen order), so a repeated ``dependsOn`` id never returns a duplicate.
"""

from __future__ import annotations

from collections.abc import Callable

from factory_console.domain import DepNeighborhood, RunState, Ticket, TicketSummary


class TicketProjection:
    """Projects a fixed ticket list to summaries and dependency neighborhoods.

    Built once per request (the real adapter) or once per fake (the in-memory
    adapter) from the materialized ``tickets`` in their natural order.
    ``run_state_for`` resolves a ticket id to its :class:`RunState` — a
    filesystem probe for the real adapter, a seeded-map lookup for the fake — and
    is the ONLY behavioral difference between the two adapters' projections.
    """

    def __init__(self, tickets: list[Ticket], run_state_for: Callable[[str], RunState]) -> None:
        self._tickets = tickets
        self._run_state_for = run_state_for
        self._by_id: dict[str, Ticket] = {ticket.id: ticket for ticket in tickets}
        self._dependents: dict[str, list[Ticket]] = {}
        for ticket in tickets:
            # ``dict.fromkeys`` collapses a ticket's own duplicate ``dependsOn`` ids
            # (order-preserving) so a ``dependsOn=["B", "B"]`` counts the ticket as a
            # dependent of B ONCE — the reverse index counts distinct dependent
            # TICKETS, not declared edges. (``depCount = len(dependsOn)`` on the
            # forward side still intentionally counts every declared edge.)
            for dep_id in dict.fromkeys(ticket.dependsOn):
                if dep_id == ticket.id:
                    continue  # a ticket is never its own dependent
                self._dependents.setdefault(dep_id, []).append(ticket)

    def _summarize(self, ticket: Ticket) -> TicketSummary:
        """Project one ``ticket`` to its :class:`TicketSummary`.

        ``runState`` comes from the injected ``run_state_for``; ``depCount`` counts
        ALL declared deps (dangling included) while ``dependentCount`` reads the
        reverse index (only OTHER tickets that name this id). Internal to the
        projection: :meth:`summaries` and :meth:`neighborhood` are the public API.
        """
        return TicketSummary(
            id=ticket.id,
            title=ticket.title,
            status=ticket.status,
            track=ticket.track,
            milestone=ticket.milestone,
            runState=self._run_state_for(ticket.id),
            depCount=len(ticket.dependsOn),
            dependentCount=len(self._dependents.get(ticket.id, [])),
        )

    def summaries(self) -> list[TicketSummary]:
        """Return a :class:`TicketSummary` for every ticket, in list order."""
        return [self._summarize(ticket) for ticket in self._tickets]

    def ticket_for(self, ticket_id: str) -> Ticket | None:
        """Return the ticket with ``id == ticket_id``, or ``None`` if absent."""
        return self._by_id.get(ticket_id)

    def neighborhood(self, ticket: Ticket) -> DepNeighborhood:
        """Build the :class:`DepNeighborhood` for ``ticket``.

        ``directDeps`` are the summaries of each ``dependsOn`` id that resolves to
        a known ticket (in first-seen ``dependsOn`` order); ``unresolvedDeps`` are
        the ids with no matching ticket (same order); ``directDependents`` are the
        OTHER tickets that depend on this one (in list order). Every summary comes
        from :meth:`_summarize`, so it matches the list view.

        Both forward lists name each NEIGHBOUR once: ``dict.fromkeys`` collapses a
        ticket's own duplicate ``dependsOn`` ids (order-preserving) the same way
        the reverse index does, so a ``dependsOn=["B", "B"]`` cannot return B
        twice — a repeated neighbour would crash the deps route's keyed ``{#each}``
        in the frontend. (``depCount = len(dependsOn)`` on the summary still
        intentionally counts every declared edge.)
        """
        distinct_dep_ids = list(dict.fromkeys(ticket.dependsOn))
        return DepNeighborhood(
            ticket=self._summarize(ticket),
            directDeps=[
                self._summarize(self._by_id[dep_id])
                for dep_id in distinct_dep_ids
                if dep_id in self._by_id
            ],
            directDependents=[
                self._summarize(dependent) for dependent in self._dependents.get(ticket.id, [])
            ],
            unresolvedDeps=[dep_id for dep_id in distinct_dep_ids if dep_id not in self._by_id],
        )
