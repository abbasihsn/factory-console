"""The read-only :class:`FileAdapter` port.

:class:`FileAdapter` is the internal seam between the HTTP handlers and
filesystem I/O: handlers depend on this ``Protocol`` (wired via
``FastAPI.Depends()``) and never call ``open()`` directly. Two implementations
satisfy it structurally — a filesystem-backed ``RealFileAdapter`` and the
in-memory :class:`~factory_console.file_adapter.fake.FakeFileAdapter` used by
tests. The port is deliberately read-only: the console never writes to the
target project.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable

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


@runtime_checkable
class FileAdapter(Protocol):
    """Read-only seam between HTTP handlers and filesystem I/O.

    Every method except :meth:`load_project` takes the resolved
    :class:`~factory_console.domain.project.Project` for the request and returns
    read-through domain entities; an implementation must not mutate the project
    or write to the target filesystem. ``@runtime_checkable`` lets tests assert
    an implementation satisfies the port with ``isinstance`` — a structural
    check on method presence only, not on signatures.
    """

    def load_project(self, root: Path) -> Project:
        """Resolve the target project rooted at ``root`` and return its :class:`Project`."""
        ...

    def list_tickets(self, project: Project) -> list[TicketSummary]:
        """Project every ticket to a :class:`TicketSummary` with run-state and edge counts."""
        ...

    def has_ticket(self, project: Project, ticket_id: str) -> bool:
        """Whether the MANIFEST carries ``ticket_id``, without reading its ``.md``.

        The existence question on its own. :meth:`get_ticket` cannot answer it:
        it enriches from disk, so a manifest entry whose body file is missing
        raises ``TicketFileMissing`` instead of returning a ticket — and a caller
        using it as an existence probe gets a 404 where the entry plainly exists.
        """
        ...

    def get_ticket(self, project: Project, ticket_id: str) -> Ticket | None:
        """Return the full :class:`Ticket` for ``ticket_id``, or ``None`` if absent."""
        ...

    def get_deps(self, project: Project, ticket_id: str) -> DepNeighborhood | None:
        """Return the :class:`DepNeighborhood` for ``ticket_id``, or ``None`` if absent."""
        ...

    def read_run_state(self, project: Project, ticket_id: str) -> RunState:
        """Return the :class:`RunState` for ``ticket_id``.

        ``unknown`` when there is no run-state source to ask, when it could not be
        trusted, or when it resolved and lists NO ticket at all (a VACUOUS source —
        an empty marker directory, or a ``run-state.json`` whose ``tickets`` object
        parsed and is empty); ``absent`` only when a source resolved, lists at least
        one ticket, and does not list THIS id. The vacuous carve-out is not optional
        for a conforming implementation: a source that names nobody exercises no
        authority over anybody, and answering ``absent`` there makes every write 409
        and turns an empty-but-valid run-state into a project-wide read-only lockout
        (T80 amendment, gap 1). ``unreadable`` is the THIRD unnamed answer and the
        only one that fails closed. It covers TWO ways the information can be
        unavailable, and a conforming implementation owes both: the source is THERE and
        its bytes or entries could not be read at all (``EACCES`` and friends), so
        nothing was learned about this id; OR it was read fine and what it says about
        THIS id could not be interpreted — a ``status`` outside the alias table, a
        non-string status, an entry that is not an object (T80 amendment 4), or a marker
        under a state subdirectory this console has no name for (T92). A conforming
        implementation must not fold either into the other two answers — "I could not
        look" and "I looked and did not understand" are both distinct from "I looked and
        there is nothing to find", and the claim saying a lane owns this ticket may be
        exactly the one that could not be read or named (T80 amendment 2).

        The three states are NOT interchangeable at the write
        gate — ``unknown`` is mutable, ``absent`` is refused 409 for an edit (though
        :func:`~factory_console.file_adapter.write_gate.ensure_deletable` permits a
        delete), and ``unreadable`` is refused 409 by BOTH gates.
        """
        ...

    def read_run_states(self, project: Project, ticket_ids: Iterable[str]) -> dict[str, RunState]:
        """Resolve MANY ids against ONE read of the run-state source.

        Every id in ``ticket_ids`` appears in the result, mapped to exactly what
        :meth:`read_run_state` would answer for it — the whole contract above applies
        verbatim, including the vacuous-source carve-out and the three unnamed answers.
        This is not a second opinion; a conforming implementation resolves both through
        one function so the batch and the single-ticket answers cannot disagree.

        It exists because the source is a FILE. ``read_run_state`` opens and parses it
        per call, which is right for the one-ticket detail path and quadratic for a view
        that asks about a whole document: the roadmap resolves one status per item, so
        looping the singular form would re-read ``run-state.json`` once per bullet. The
        list and dependency views already avoid this internally by building their
        resolver once per request; this is that same economy made available to a caller
        outside the ticket projection.

        Two divergences from the singular form, both because this answers about a SET:

        * An id repeated in ``ticket_ids`` is resolved once and appears once — the
          result is keyed by id, so a roadmap that names one ticket in two milestones
          costs one lookup and cannot report two different states for it.
        * A path-unsafe id degrades to :attr:`RunState.unreadable` instead of raising
          :class:`~factory_console.file_adapter.path_safety.PathTraversal`, matching how
          the list view already treats one, and for the same reason: a single malformed
          id must not fail the whole request with a 400 that names no bad input. It
          degrades to the REFUSING state, never the mutable ``unknown`` — the check did
          not run, which is "unavailable", not "nothing was said".

        An empty ``ticket_ids`` returns ``{}`` without touching the source at all.
        """
        ...

    def read_lane_phase(self, project: Project, ticket_id: str) -> str | None:
        """Return WHERE ``ticket_id``'s running lane has got to, or ``None``.

        One of ``building``, ``accepting``, ``reviewing``, ``fixing``, ``verifying`` —
        the factory's own lane steps, in order. A lane holds its worktree for up to 90
        minutes, and ``in_progress`` alone is a 90-minute black box in the one place an
        operator most wants a reading: still building, or stuck in review for an hour?

        **A conforming implementation returns the string it read, unvalidated.** This is
        the deliberate opposite of how an unrecognised STATUS is handled — that resolves
        the refusing ``unreadable`` and every write is denied. A phase is displayed and
        never branched on, so an unrecognised one costs an odd label; refusing it would
        blank a field the operator is watching, and escalating it would deny writes on a
        ticket whose status read perfectly. A cosmetic field must not become a write
        lockout, and an implementation that made one would be wrong.

        ``None`` is every way there is no phase, and unlike the run-state answers those
        ways are NOT told apart: no source, a directory source (the marker layout has
        nowhere to record one), an unreadable file, an id the source does not name, and —
        the common case — a ticket that is not mid-lane, which the factory writes as an
        explicit ``null`` on every status transition. Nothing gates on a phase, so no
        caller needs to distinguish them.

        Reading a phase makes no claim about the STATUS. An implementation must not let
        this read alter, refuse, or be refused by :meth:`read_run_state`.
        """
        ...

    def read_subversion(self, project: Project) -> Subversion | None:
        """Return the open sub-version, or ``None`` when none is open.

        v3's one recurring human gate: tickets auto-merge onto a single
        ``factory/<sub-version>`` branch and the factory HOLDS at that branch's PR
        waiting for a human. Only one is ever open.

        ``None`` is the NORMAL state between cuts, not a failure — a conforming
        implementation returns it for a project with no source, a directory source (v2
        had no sub-version to hold at), a source it could not read, and a document with
        no ``subversion`` record. A caller must render ``None`` as nothing at all rather
        than as an empty or broken strip.
        """
        ...

    def get_roadmap(self, project: Project) -> Roadmap | None:
        """Return the project :class:`Roadmap`, or ``None`` when the project has none."""
        ...

    def search_tickets(
        self, project: Project, query: str, *, limit: int | None = None
    ) -> list[SearchHit]:
        """Rank tickets by ``query`` over id/title/``provides``/body, best first.

        Returns a :class:`~factory_console.domain.search.SearchHit` per matching
        ticket, ordered by descending relevance score; a blank or whitespace-only
        query returns ``[]``. ``limit`` truncates to the first ``limit`` hits when
        not ``None``.
        """
        ...

    def get_graph(self, project: Project) -> TicketGraph:
        """Project the whole ticket set to the run-state-coloured dependency DAG.

        Returns a :class:`~factory_console.domain.graph.TicketGraph`: one node per
        ticket (carrying the same run-state as :meth:`list_tickets`) and one edge
        per RESOLVED ``dependsOn`` relation — self-loops dropped, dangling ids
        omitted, duplicates collapsed.
        """
        ...
