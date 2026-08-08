"""Roadmap application service — the document, with live status joined onto it.

:class:`RoadmapService` is the roadmap's counterpart to
:class:`~factory_console.services.ticket_service.TicketService`: the adapter reads and
parses ``ROADMAP.md``, and this joins each item's run-state on so the HTTP handler stays
thin. It depends only on the read-only
:class:`~factory_console.file_adapter.protocol.FileAdapter` port.

**Why the join is here and not in the parser.** The roadmap document says what the work
IS; the run-state source says how far it has got. Those are two files with two owners —
a human writes the first, the factory writes the second — and the parser must not learn
to read the second, or "what does this project plan to do" becomes unanswerable without
a factory. Keeping the join in a service is also what makes the status LIVE: it is
resolved per request, so a lane that merged a ticket a second ago is reflected on the
next page load with nothing to re-commit.

This is the change App Factory v3 §4 asks for, stated in the console's own roadmap
before v3 existed: *"the durable fix is for the roadmap view to read run-state directly
… not for someone to remember."* What it replaces is a hand-ticked ``[x]`` in the
document — derived state in a committed file, which ``factory-doctor`` FAILs a
repository for carrying, and which is wrong the moment nobody remembers to tick it.
"""

from __future__ import annotations

from factory_console.domain import Project, Roadmap, RunState
from factory_console.domain.deps import RoadmapMilestone
from factory_console.file_adapter.protocol import FileAdapter


class RoadmapService:
    """Reads the project roadmap and resolves each item's run-state.

    Constructed per request with the injected adapter; holds no state beyond it.
    """

    def __init__(self, adapter: FileAdapter) -> None:
        self._adapter = adapter

    def get_roadmap(self, project: Project) -> Roadmap | None:
        """Return the project's :class:`Roadmap` with ``runState`` on every item.

        ``None`` when the project has no roadmap, propagating the adapter's answer
        unchanged. A :class:`~factory_console.file_adapter.real.RoadmapUnreadable` from
        the read propagates too — this method adds a join, not error handling.

        Every ticket named anywhere in the document is resolved in ONE pass, through
        :meth:`~factory_console.file_adapter.protocol.FileAdapter.read_run_states`. The
        singular ``read_run_state`` would re-open the run-state file once per bullet:
        this repository's own roadmap carries 141 items, so the difference is one read
        against a hundred and forty-one, for an answer that cannot vary between them.

        An item that names no ticket keeps ``runState=None``. That is not the same as
        :attr:`~factory_console.domain.run_state.RunState.unknown`, and the distinction
        is the reason ``None`` is representable at all: ``unknown`` says a source was
        asked and said nothing, while ``None`` says there was no question — a prose
        bullet or a section label has no status because there is no ticket to have one.
        Badging those ``Unknown`` would fill the view with claims about tickets that do
        not exist.
        """
        roadmap = self._adapter.get_roadmap(project)
        if roadmap is None:
            return None

        ticket_ids = [
            item.ticketId
            for milestone in roadmap.milestones
            for item in milestone.items
            if item.ticketId is not None
        ]
        states = self._adapter.read_run_states(project, ticket_ids)
        return roadmap.model_copy(
            update={
                "milestones": [
                    self._join_milestone(milestone, states) for milestone in roadmap.milestones
                ]
            }
        )

    @staticmethod
    def _join_milestone(
        milestone: RoadmapMilestone, states: dict[str, RunState]
    ) -> RoadmapMilestone:
        """Return ``milestone`` with each ticket-bearing item's ``runState`` filled in.

        ``states[...]`` is INDEXED, not ``.get``-ed with a fallback. The port promises
        every requested id back, so a missing key is a broken adapter — and the fallback
        would be ``None``, which this model reads as "this item names no ticket". A
        silent downgrade from a resolution failure to "there was nothing to resolve" is
        exactly the kind of quiet wrong answer the run-state vocabulary exists to
        prevent, so let it raise instead.

        An item with no ``ticketId`` is returned UNCHANGED rather than copied with
        ``runState=None`` — it already is ``None``, and a copy would allocate a new
        frozen model to write the value it already holds.
        """
        return milestone.model_copy(
            update={
                "items": [
                    item
                    if item.ticketId is None
                    else item.model_copy(update={"runState": states[item.ticketId]})
                    for item in milestone.items
                ]
            }
        )
