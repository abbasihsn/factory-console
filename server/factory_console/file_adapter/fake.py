"""In-memory :class:`FakeFileAdapter` — a side-effect-free FileAdapter for tests.

The fake is seeded with a :class:`Project`, a list of :class:`Ticket` s, an
optional ``{ticket_id: RunState}`` map, and an optional :class:`Roadmap`, then
answers every :class:`~factory_console.file_adapter.protocol.FileAdapter` method
as a pure read over that in-memory data — no filesystem access, no I/O, and no
mutation of the seeded values.

The list view and the dependency view share ONE
:class:`~factory_console.file_adapter.projection.TicketProjection` — the SAME
projection class the real, filesystem-backed adapter uses, with only the
run-state source injected differently (a seeded-map lookup here, an on-disk probe
there) — so the two views can never disagree and cannot drift from the real
adapter. The two derived semantics that projection pins (and the unit tests
cover):

* ``depCount`` is ``len(ticket.dependsOn)`` — the count of ALL declared direct
  dependencies, including ids that do not resolve to a seeded ticket (a dangling
  edge still counts as a declared dependency).
* the reverse index (``dependentCount`` and ``directDependents``) counts only
  OTHER seeded tickets that name the id in their ``dependsOn`` — a ticket is
  never its own dependent, so a self-referential edge does not inflate it (that
  edge does still count toward the ticket's own ``depCount``).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from factory_console.domain import (
    DepNeighborhood,
    Project,
    Roadmap,
    RunState,
    Ticket,
    TicketSummary,
)
from factory_console.domain.graph import TicketGraph
from factory_console.domain.search import SearchHit
from factory_console.domain.subversion import Subversion
from factory_console.file_adapter.graph import build_graph
from factory_console.file_adapter.projection import TicketProjection
from factory_console.file_adapter.search import rank_tickets, to_search_hits


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
        phases: dict[str, str] | None = None,
        subversion: Subversion | None = None,
    ) -> None:
        """Seed the fake with pre-resolved project data.

        ``run_states`` is normalized to ``{}`` when ``None`` (every ticket then
        resolves to :attr:`RunState.unknown`). The seeded ``tickets`` order is
        preserved for every list-shaped result; the shared
        :class:`~factory_console.file_adapter.projection.TicketProjection` builds
        the ``{id: Ticket}`` lookup and the reverse dependents index and backs
        both the list and dependency views, with run-state resolved from the
        seeded map.

        ``phases`` maps ``{ticket_id: phase}`` for the lane steps an ``in_progress``
        ticket has reached; an unseeded id answers ``None``, which is what the real
        adapter answers for a ticket that is not mid-lane. ``subversion`` is the open
        sub-version record, defaulting to ``None`` — the normal state between cuts, and
        the one every test that does not care about the gate wants.
        """
        self._project = project
        self._tickets = tickets
        self._run_states = {} if run_states is None else run_states
        self._roadmap = roadmap
        self._phases = {} if phases is None else phases
        self._subversion = subversion
        self._projection = TicketProjection(
            tickets,
            run_state_for=lambda ticket_id: self._run_states.get(ticket_id, RunState.unknown),
            phase_for=lambda ticket_id: self._phases.get(ticket_id),
        )

    def load_project(self, root: Path) -> Project:
        """Return the seeded :class:`Project`.

        ``root`` is accepted to satisfy the Protocol but ignored: the in-memory
        fake is pre-seeded with its project and performs no discovery.
        """
        return self._project

    def list_tickets(self, project: Project) -> list[TicketSummary]:
        """Return a :class:`TicketSummary` for every seeded ticket, in seeded order."""
        return self._projection.summaries()

    def get_ticket(self, project: Project, ticket_id: str) -> Ticket | None:
        """Return the seeded :class:`Ticket` for ``ticket_id``, or ``None`` if unseeded."""
        return self._projection.ticket_for(ticket_id)

    def has_ticket(self, project: Project, ticket_id: str) -> bool:
        """Whether ``ticket_id`` is seeded. The fake has no ``.md`` files to miss."""
        return self._projection.ticket_for(ticket_id) is not None

    def get_deps(self, project: Project, ticket_id: str) -> DepNeighborhood | None:
        """Return the :class:`DepNeighborhood` for ``ticket_id``, or ``None`` if unseeded.

        ``directDeps`` are the summaries of each distinct ``dependsOn`` id that
        resolves to a seeded ticket (in first-seen ``dependsOn`` order);
        ``unresolvedDeps`` are the distinct ``dependsOn`` ids with no seeded
        ticket (same order); ``directDependents``
        are the summaries of every OTHER seeded ticket that depends on
        ``ticket_id`` (in seeded order).
        """
        ticket = self._projection.ticket_for(ticket_id)
        if ticket is None:
            return None
        return self._projection.neighborhood(ticket)

    def read_run_state(self, project: Project, ticket_id: str) -> RunState:
        """Return the seeded run-state for ``ticket_id``, else :attr:`RunState.unknown`.

        Non-optional: an unseeded id (or a ticket with no seeded run-state) yields
        :attr:`RunState.unknown` rather than ``None``.

        That default diverges from :class:`RealFileAdapter` since T80: against a
        project with a resolved run-state source the real adapter answers
        :attr:`RunState.absent` for an id the source does not list, and ``absent`` is
        refused by the write gate while ``unknown`` is not. Seed the state explicitly
        when a test's subject is that distinction.
        """
        return self._run_states.get(ticket_id, RunState.unknown)

    def read_run_states(self, project: Project, ticket_ids: Iterable[str]) -> dict[str, RunState]:
        """Resolve many ids against the seeded map, one entry per DISTINCT id.

        Delegates to :meth:`read_run_state` per id rather than reading
        ``self._run_states`` directly, so the two answers cannot drift — the same
        property the real adapter gets from sharing one resolver, obtained here the
        cheap way because a dict lookup has no source to re-read.

        The real adapter's other two divergences do not arise: a seeded map has no
        filesystem, so no id is path-unsafe and nothing degrades. Deduplication is kept
        because it is part of the port's contract (a repeated id appears once), not an
        artifact of how the real one avoids re-reading a file.
        """
        return {
            ticket_id: self.read_run_state(project, ticket_id)
            for ticket_id in dict.fromkeys(ticket_ids)
        }

    def read_lane_phase(self, project: Project, ticket_id: str) -> str | None:
        """Return the seeded lane phase for ``ticket_id``, else ``None``.

        Seeded through ``phases`` and defaulting to ``None`` for every unseeded id —
        which is what the real adapter answers for a ticket that is not mid-lane, the
        overwhelmingly common case. Unlike ``run_states`` there is no divergence to warn
        about: a phase has no third answer and no gate reads it.
        """
        return self._phases.get(ticket_id)

    def read_subversion(self, project: Project) -> Subversion | None:
        """Return the seeded open :class:`Subversion`, or ``None`` when seeded without one."""
        return self._subversion

    def get_roadmap(self, project: Project) -> Roadmap | None:
        """Return the seeded :class:`Roadmap`, or ``None`` when seeded without one."""
        return self._roadmap

    def search_tickets(
        self, project: Project, query: str, *, limit: int | None = None
    ) -> list[SearchHit]:
        """Rank the seeded tickets by ``query`` over id/title/``provides``/body.

        Ranks the in-memory seeded tickets (whose ``bodyMarkdown`` is already
        populated) via the SAME pure
        :func:`~factory_console.file_adapter.search.rank_tickets` the real adapter
        uses, then re-keys each
        :class:`~factory_console.file_adapter.search.ScoredTicket` to a
        :class:`~factory_console.domain.search.SearchHit` via the shared
        projection's summaries, so each hit carries run-state resolved from the
        seeded map. A blank query yields ``[]``; ``limit`` truncates to the first
        ``limit`` hits when not ``None``.
        """
        summary_by_id = {summary.id: summary for summary in self._projection.summaries()}
        return to_search_hits(rank_tickets(self._tickets, query), summary_by_id, limit)

    def get_graph(self, project: Project) -> TicketGraph:
        """Build the whole-project dependency DAG from the shared seeded projection.

        Reuses the SAME projection as :meth:`list_tickets` / :meth:`get_deps`, so a
        node's ``runState`` matches those views, then delegates to the pure
        :func:`~factory_console.file_adapter.graph.build_graph`.
        """
        return build_graph(self._projection)
