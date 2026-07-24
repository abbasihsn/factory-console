"""Filesystem-backed :class:`RealFileAdapter` — the production FileAdapter.

This is the adapter the CLI wires up. It *composes* the small, single-purpose
file-adapter modules — :mod:`~factory_console.file_adapter.discovery`,
:mod:`~factory_console.file_adapter.manifest`,
:mod:`~factory_console.file_adapter.ticket_md`,
:mod:`~factory_console.file_adapter.markdown_render`, and
:mod:`~factory_console.file_adapter.run_state` — into the seven-method read-only
:class:`~factory_console.file_adapter.protocol.FileAdapter` port, rather than
re-implementing manifest parsing, ``.md`` reading, rendering, or run-state
probing here.

The adapter is *stateless*: ``RealFileAdapter()`` takes no arguments and stores
no project data, so every method re-reads the target filesystem — no cache, no
watcher (see ``ARCHITECTURE.md`` "every request re-reads"). It is also strictly
*read-only*: it reads with :meth:`Path.read_text` (never ``open()``) and never
creates, writes, or deletes anything under the target project.

The list view (:meth:`RealFileAdapter.list_tickets`) and the dependency view
(:meth:`RealFileAdapter.get_deps`) share ONE per-request
:class:`~factory_console.file_adapter.projection.TicketProjection` — the SAME
projection class the
:class:`~factory_console.file_adapter.fake.FakeFileAdapter` uses, with only the
run-state source injected differently — so a ticket's ``runState`` and edge
counts can never disagree between the two views or drift from the fake.
"""

from __future__ import annotations

import logging
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
from factory_console.domain.graph import TicketGraph
from factory_console.domain.search import SearchHit
from factory_console.errors import FactoryConsoleError
from factory_console.file_adapter.discovery import find_project_root
from factory_console.file_adapter.graph import build_graph
from factory_console.file_adapter.manifest import iter_ticket_stubs
from factory_console.file_adapter.markdown_render import render_markdown, render_ticket_html
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.projection import TicketProjection
from factory_console.file_adapter.run_state import find_run_state_dir, probe_ticket_state
from factory_console.file_adapter.search import rank_tickets, to_search_hits
from factory_console.file_adapter.ticket_md import (
    TicketFileMissing,
    TicketFileUnreadable,
    enrich_ticket,
    read_ticket_md,
)

_LOGGER = logging.getLogger(__name__)

_ROADMAP_RELPATHS = (Path("ROADMAP.md"), Path("docs") / "ROADMAP.md")
"""Documented roadmap locations, probed in order: project root, then ``docs/``."""


class RoadmapUnreadable(FactoryConsoleError):
    """A discovered ``ROADMAP.md`` exists but cannot be read as UTF-8 text.

    The roadmap's own read-failure envelope, mirroring
    :class:`~factory_console.file_adapter.ticket_md.TicketFileUnreadable` for
    ticket bodies and :class:`~factory_console.file_adapter.manifest.MalformedManifest`
    for the manifest: a permission-denied read or a vanished file
    (:class:`OSError`) and non-UTF-8 bytes (:class:`UnicodeDecodeError`) are
    server-side data problems, so they map to HTTP 500 rather than escaping as an
    unmapped 500. ``details`` carries the roadmap ``path`` — which discovery
    already surfaces on ``Project.roadmapPath`` — never the file contents.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(
            code="roadmap_unreadable",
            message=f"Roadmap at {path} could not be read as UTF-8 text",
            status=500,
            details={"path": str(path)},
        )
        self.path = path


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

        Raises :class:`RoadmapUnreadable` when the discovered file cannot be read
        as UTF-8 (non-UTF-8 bytes, a permission-denied read, or a file that
        vanished after discovery), so the failure surfaces as a mapped envelope
        instead of an unmapped 500 — the same read guard ``read_ticket_md`` and
        ``load_manifest`` use.
        """
        if project.roadmapPath is None:
            return None
        try:
            body = project.roadmapPath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise RoadmapUnreadable(project.roadmapPath) from exc
        return Roadmap(
            path=project.roadmapPath,
            bodyMarkdown=body,
            bodyHtml=render_markdown(body),
        )

    def search_tickets(
        self, project: Project, query: str, *, limit: int | None = None
    ) -> list[SearchHit]:
        """Rank every manifest ticket by ``query`` over id/title/``provides``/body.

        Materializes the manifest stubs, builds the SAME shared
        :class:`~factory_console.file_adapter.projection.TicketProjection` the
        list view uses (so each hit's summary carries the identical run-state and
        edge counts), then enriches EACH stub with its on-disk body TOLERANTLY:
        :func:`~factory_console.file_adapter.ticket_md.read_ticket_md`'s
        ``TicketFileMissing`` / ``TicketFileUnreadable`` / ``PathTraversal`` fall
        back to an empty body so one bad ``.md`` degrades to an id/title/provides
        match rather than failing the whole scan. Ranking is delegated to the pure
        :func:`~factory_console.file_adapter.search.rank_tickets`; each
        :class:`~factory_console.file_adapter.search.ScoredTicket` is re-keyed to a
        :class:`~factory_console.domain.search.SearchHit` via the projection's
        summaries, then ``limit`` truncates to the first ``limit`` hits.

        Consistent with ``ARCHITECTURE.md`` "every request re-reads": this
        re-reads every ticket ``.md`` per call with no cache or index (an
        in-memory index is deferred to a later milestone alongside the watcher).

        A blank or whitespace-only ``query`` short-circuits to ``[]`` BEFORE any
        filesystem work — ``rank_tickets`` would return ``[]`` for it anyway, so
        probing run-state and reading every ``.md`` first (the path a cleared or
        momentarily-empty search box hits) would be a full scan thrown away. The
        "every request re-reads" tradeoff is about lacking a cache for real
        queries, not about scanning for a guaranteed-empty result.
        """
        if not query.split():
            return []
        stubs = list(iter_ticket_stubs(project))
        projection = self._projection_for(project, stubs)
        summary_by_id = {summary.id: summary for summary in projection.summaries()}
        enriched = [
            stub.model_copy(update={"bodyMarkdown": self._safe_body(project, stub.id)})
            for stub in stubs
        ]
        return to_search_hits(rank_tickets(enriched, query), summary_by_id, limit)

    def get_graph(self, project: Project) -> TicketGraph:
        """Build the whole-project dependency DAG from the shared per-request projection.

        Reuses the SAME projection as :meth:`list_tickets` / :meth:`get_deps` via
        :meth:`_project_manifest`, so a node's ``runState`` can never drift from
        those views, then delegates to the pure
        :func:`~factory_console.file_adapter.graph.build_graph`. Consistent with
        ``ARCHITECTURE.md`` "every request re-reads": this re-reads the manifest
        per call with no cache.
        """
        return build_graph(self._project_manifest(project))

    @staticmethod
    def _safe_body(project: Project, ticket_id: str) -> str:
        """Read ``ticket_id``'s ``.md`` body, degrading a bad ``.md`` to ``""``.

        A missing file, an unreadable file, or an unsafe id
        (``TicketFileMissing`` / ``TicketFileUnreadable`` / ``PathTraversal``)
        yields an empty body so a single broken ticket ``.md`` never fails the
        whole search scan — the ticket can still match on id/title/``provides``.

        The tolerance is observable, not silent: a legitimately absent ``.md``
        (``TicketFileMissing``) is the routine case and logs at ``debug``, while
        an *unreadable* file (a data/permission problem) or a tripped
        ``PathTraversal`` guard (a manifest id resolving outside the project root)
        logs at ``warning`` — so a corrupt file or a security-relevant bad id
        leaves a trace instead of a silently dropped body match.
        """
        try:
            _front_matter, body = read_ticket_md(project, ticket_id)
        except TicketFileMissing:
            _LOGGER.debug("search: ticket %s has no .md; scanning with empty body", ticket_id)
            return ""
        except (TicketFileUnreadable, PathTraversal) as exc:
            _LOGGER.warning(
                "search: could not read body for ticket %s (%s); scanning with empty body",
                ticket_id,
                type(exc).__name__,
                extra={"ticket_id": ticket_id},
            )
            return ""
        return body

    @staticmethod
    def _find_roadmap(root: Path) -> Path | None:
        """Return the first existing ``ROADMAP.md`` (root then ``docs/``), else ``None``."""
        for relpath in _ROADMAP_RELPATHS:
            candidate = root / relpath
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _safe_run_state(run_state_dir: Path | None, ticket_id: str) -> RunState:
        """Resolve run-state for the LIST/DEPS projection, degrading a path-unsafe id.

        ``list_tickets`` / ``get_deps`` probe run-state for EVERY ticket, so a single
        malformed id — a bare ``.`` or ``..``, which ``TICKET_ID_PATTERN`` admits as a
        character class yet is a single-segment traversal — letting
        :func:`~factory_console.file_adapter.run_state.probe_ticket_state` raise
        :class:`~factory_console.file_adapter.path_safety.PathTraversal` would fail the
        WHOLE request with a 400 that names no bad input. Map it to
        :attr:`RunState.unknown` instead: that ticket shows an ``unknown`` badge rather
        than crashing its neighbours' listing. The hard traversal guard still protects
        the single-ticket :meth:`read_run_state` filesystem read.
        """
        try:
            return probe_ticket_state(run_state_dir, ticket_id)
        except PathTraversal:
            return RunState.unknown

    @staticmethod
    def _project_manifest(project: Project) -> TicketProjection:
        """Materialize the manifest stubs and wrap them in a per-request projection.

        Run-state is resolved lazily by probing the project's run-state directory,
        the one behavioral difference from the fake adapter's seeded-map lookup. A
        :class:`~factory_console.file_adapter.manifest.MalformedManifest` from the
        manifest read propagates to the caller.
        """
        return RealFileAdapter._projection_for(project, list(iter_ticket_stubs(project)))

    @staticmethod
    def _projection_for(project: Project, stubs: list[Ticket]) -> TicketProjection:
        """Wrap already-materialized ``stubs`` in the per-request projection.

        The SINGLE run-state-wiring point the list/deps view
        (:meth:`_project_manifest`) and the search scan (:meth:`search_tickets`)
        both call, so the run-state resolution that makes summaries agree across
        the views can never drift between them. ``search_tickets`` passes the
        stubs it already read (one manifest read per request); ``_project_manifest``
        materializes them itself.
        """
        return TicketProjection(
            stubs,
            run_state_for=lambda ticket_id: RealFileAdapter._safe_run_state(
                project.runStateDir, ticket_id
            ),
        )
