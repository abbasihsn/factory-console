"""Filesystem-backed :class:`RealFileAdapter` — the production FileAdapter.

This is the adapter the CLI wires up. It *composes* the small, single-purpose
file-adapter modules — :mod:`~factory_console.file_adapter.discovery`,
:mod:`~factory_console.file_adapter.manifest`,
:mod:`~factory_console.file_adapter.ticket_md`,
:mod:`~factory_console.file_adapter.markdown_render`, and
:mod:`~factory_console.file_adapter.run_state` — into the six-method read-only
:class:`~factory_console.file_adapter.protocol.FileAdapter` port, rather than
re-implementing manifest parsing, ``.md`` reading, rendering, or run-state
probing here.

The adapter is *stateless*: ``RealFileAdapter()`` takes no arguments and stores
no project data, so every method re-reads the target filesystem — no cache, no
watcher (see ``ARCHITECTURE.md`` "every request re-reads"). It is also strictly
*read-only*: it reads with :meth:`Path.read_text` (never ``open()``) and never
creates, writes, or deletes anything under the target project.

The list view (:meth:`RealFileAdapter.list_tickets`) and the dependency view
(:meth:`RealFileAdapter.get_deps`) share ONE per-request projection
(:class:`_ManifestProjection`) — mirroring
:class:`~factory_console.file_adapter.fake.FakeFileAdapter` — so a ticket's
``runState`` and edge counts can never disagree between the two views.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from factory_console.domain import (
    DepNeighborhood,
    Project,
    Roadmap,
    RunState,
    Ticket,
    TicketSummary,
)
from factory_console.file_adapter.discovery import find_project_root
from factory_console.file_adapter.manifest import iter_ticket_stubs
from factory_console.file_adapter.markdown_render import render_markdown, render_ticket_html
from factory_console.file_adapter.run_state import find_run_state_dir, probe_ticket_state
from factory_console.file_adapter.ticket_md import enrich_ticket

_ROADMAP_RELPATHS = (Path("ROADMAP.md"), Path("docs") / "ROADMAP.md")
"""Documented roadmap locations, probed in order: project root, then ``docs/``."""


class _ManifestProjection:
    """Per-request projection of a project's manifest to :class:`TicketSummary`.

    Built once per :meth:`RealFileAdapter.list_tickets` /
    :meth:`RealFileAdapter.get_deps` call from the materialized manifest stubs,
    this is the SINGLE source of the ticket projection shared by both views
    (mirroring :class:`~factory_console.file_adapter.fake.FakeFileAdapter`'s
    in-memory indexes) so the list view and the dependency view can never drift.
    It holds a ``{id: Ticket}`` lookup and a reverse ``{id: [dependent Ticket]}``
    index in manifest order with self-edges excluded, and probes the run-state
    directory on demand in :meth:`summarize`.
    """

    def __init__(self, project: Project, tickets: list[Ticket]) -> None:
        self._project = project
        self._tickets = tickets
        self._by_id: dict[str, Ticket] = {ticket.id: ticket for ticket in tickets}
        self._dependents: dict[str, list[Ticket]] = {}
        for ticket in tickets:
            for dep_id in ticket.dependsOn:
                if dep_id == ticket.id:
                    continue  # a ticket is never its own dependent
                self._dependents.setdefault(dep_id, []).append(ticket)

    def summarize(self, ticket: Ticket) -> TicketSummary:
        """Project one manifest ``ticket`` to its :class:`TicketSummary`.

        The single shared projection used by BOTH views. ``runState`` is resolved
        by probing the project's run-state directory; ``depCount`` counts ALL
        declared deps (dangling ids included) while ``dependentCount`` reads the
        reverse index (only OTHER tickets that name this id) — the two derived
        semantics pinned by :class:`FakeFileAdapter`.
        """
        return TicketSummary(
            id=ticket.id,
            title=ticket.title,
            status=ticket.status,
            track=ticket.track,
            milestone=ticket.milestone,
            runState=probe_ticket_state(self._project.runStateDir, ticket.id),
            depCount=len(ticket.dependsOn),
            dependentCount=len(self._dependents.get(ticket.id, [])),
        )

    def summaries(self) -> list[TicketSummary]:
        """Return a :class:`TicketSummary` for every ticket, in manifest order."""
        return [self.summarize(ticket) for ticket in self._tickets]

    def ticket_for(self, ticket_id: str) -> Ticket | None:
        """Return the manifest ticket with ``id == ticket_id``, or ``None`` if absent."""
        return self._by_id.get(ticket_id)

    def neighborhood(self, ticket: Ticket) -> DepNeighborhood:
        """Build the :class:`DepNeighborhood` for a manifest ``ticket``.

        ``directDeps`` are the summaries of each ``dependsOn`` id that resolves to
        a manifest ticket (in ``dependsOn`` order); ``unresolvedDeps`` are the ids
        with no matching ticket (same order); ``directDependents`` are the OTHER
        tickets that depend on this one (in manifest order). Every summary comes
        from :meth:`summarize`, so it matches the list view.
        """
        return DepNeighborhood(
            ticket=self.summarize(ticket),
            directDeps=[
                self.summarize(self._by_id[dep_id])
                for dep_id in ticket.dependsOn
                if dep_id in self._by_id
            ],
            directDependents=[
                self.summarize(dependent) for dependent in self._dependents.get(ticket.id, [])
            ],
            unresolvedDeps=[dep_id for dep_id in ticket.dependsOn if dep_id not in self._by_id],
        )


class RealFileAdapter:
    """Filesystem-backed :class:`FileAdapter` — the production adapter.

    Stateless (``RealFileAdapter()`` takes no arguments and caches nothing) and
    read-only: it satisfies the ``@runtime_checkable``
    :class:`~factory_console.file_adapter.protocol.FileAdapter` Protocol
    structurally, so ``isinstance(RealFileAdapter(), FileAdapter)`` holds without
    inheritance. Every method re-reads the target filesystem through the composed
    modules; none writes to it.
    """

    def load_project(self, root: Path) -> Project:
        """Discover the project at (or above) ``root`` and resolve its paths.

        Delegates discovery to
        :func:`~factory_console.file_adapter.discovery.find_project_root`, which
        validates the tickets manifest exists and returns the resolved root — or
        raises :class:`~factory_console.file_adapter.discovery.ProjectNotFound`,
        which propagates. ``roadmapPath`` is the first of ``ROADMAP.md`` at the
        root or under ``docs/`` that is a file, else ``None``; ``runStateDir`` is
        resolved via
        :func:`~factory_console.file_adapter.run_state.find_run_state_dir`;
        ``discoveredAt`` is stamped timezone-aware in UTC.
        """
        resolved = find_project_root(root)
        return Project(
            rootPath=resolved,
            ticketsManifestPath=resolved / "docs" / "planning" / "tickets.json",
            ticketsDir=resolved / "docs" / "planning" / "tickets",
            roadmapPath=self._find_roadmap(resolved),
            runStateDir=find_run_state_dir(resolved),
            discoveredAt=datetime.now(UTC),
        )

    def list_tickets(self, project: Project) -> list[TicketSummary]:
        """Project every manifest ticket to a :class:`TicketSummary`, in manifest order.

        Materializes the manifest stubs (a
        :class:`~factory_console.file_adapter.manifest.MalformedManifest`
        propagates), then maps the shared projection over them so each summary's
        ``runState`` and edge counts match the dependency view.
        """
        return self._project_manifest(project).summaries()

    def get_ticket(self, project: Project, ticket_id: str) -> Ticket | None:
        """Return the full :class:`Ticket` for ``ticket_id``, or ``None`` if absent.

        Looks the id up in the manifest WITHOUT touching the ticket ``.md`` files;
        an id absent from the manifest returns ``None``. A present id is enriched
        with its on-disk body via
        :func:`~factory_console.file_adapter.ticket_md.enrich_ticket` and rendered
        to ``bodyHtml`` via
        :func:`~factory_console.file_adapter.markdown_render.render_ticket_html`,
        yielding a full :class:`Ticket` with ``bodyMarkdown``, ``bodyHtml``, and
        ``raw['frontMatter']``. A missing ``.md`` or unsafe id surfaces as
        ``TicketFileMissing`` / ``PathTraversal`` (a real 404/400, not ``None``)
        and propagates.
        """
        stub = next(
            (stub for stub in iter_ticket_stubs(project) if stub.id == ticket_id),
            None,
        )
        if stub is None:
            return None
        return render_ticket_html(enrich_ticket(project, stub))

    def get_deps(self, project: Project, ticket_id: str) -> DepNeighborhood | None:
        """Return the :class:`DepNeighborhood` for ``ticket_id``, or ``None`` if absent.

        Reuses the SAME projection as :meth:`list_tickets`, so ``directDeps`` /
        ``directDependents`` carry summaries identical to the list view.
        """
        projection = self._project_manifest(project)
        ticket = projection.ticket_for(ticket_id)
        if ticket is None:
            return None
        return projection.neighborhood(ticket)

    def read_run_state(self, project: Project, ticket_id: str) -> RunState:
        """Resolve ``ticket_id``'s :class:`RunState` by probing the run-state directory.

        Delegates to
        :func:`~factory_console.file_adapter.run_state.probe_ticket_state`; a
        :class:`~factory_console.file_adapter.path_safety.PathTraversal` for an
        unsafe id propagates per that contract.
        """
        return probe_ticket_state(project.runStateDir, ticket_id)

    def get_roadmap(self, project: Project) -> Roadmap | None:
        """Return the project :class:`Roadmap`, or ``None`` when it has no roadmap.

        ``None`` when ``project.roadmapPath`` is ``None`` (no roadmap discovered);
        otherwise reads the file with :meth:`Path.read_text` and renders the body
        via :func:`~factory_console.file_adapter.markdown_render.render_markdown`.
        """
        if project.roadmapPath is None:
            return None
        body = project.roadmapPath.read_text(encoding="utf-8")
        return Roadmap(
            path=project.roadmapPath,
            bodyMarkdown=body,
            bodyHtml=render_markdown(body),
        )

    @staticmethod
    def _find_roadmap(root: Path) -> Path | None:
        """Return the first existing ``ROADMAP.md`` (root then ``docs/``), else ``None``."""
        for relpath in _ROADMAP_RELPATHS:
            candidate = root / relpath
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _project_manifest(project: Project) -> _ManifestProjection:
        """Materialize the manifest stubs and wrap them in a per-request projection.

        A :class:`~factory_console.file_adapter.manifest.MalformedManifest` from
        the manifest read propagates to the caller.
        """
        return _ManifestProjection(project, list(iter_ticket_stubs(project)))
