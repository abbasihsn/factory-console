"""Whole-project dependency-graph application service.

:class:`GraphService` holds the request logic for the ``/graph`` endpoint so the
HTTP handler stays thin: it asks the adapter for the whole-project
:class:`~factory_console.domain.graph.TicketGraph` and returns it verbatim. It
delegates graph-building straight to the port — the run-state-coloured nodes and
the resolved, self-loop-free, de-duplicated ``dependsOn`` edges all come from the
one shared
:class:`~factory_console.file_adapter.projection.TicketProjection` that backs the
list and deps views too, so the graph can never drift from them and the service
adds no second copy of that logic. Unlike
:class:`~factory_console.services.deps_service.DepsService` it raises nothing:
``get_graph`` is non-optional (the whole-project projection always exists). The
service depends only on the read-only
:class:`~factory_console.file_adapter.protocol.FileAdapter` port, never on a
concrete adapter or the filesystem.
"""

from __future__ import annotations

from factory_console.domain import Project
from factory_console.domain.graph import TicketGraph
from factory_console.file_adapter.protocol import FileAdapter


class GraphService:
    """Resolves the whole-project dependency graph over a ``FileAdapter``.

    Constructed per request with the injected adapter; holds no state beyond it.
    """

    def __init__(self, adapter: FileAdapter) -> None:
        self._adapter = adapter

    def get_graph(self, project: Project) -> TicketGraph:
        """Return the whole-project :class:`TicketGraph`.

        Delegates directly to ``adapter.get_graph`` — a first-class method on the
        ``FileAdapter`` port whose graph-building is centralized in the shared
        :class:`~factory_console.file_adapter.projection.TicketProjection`, so the
        graph, list, and deps views cannot drift. Never returns ``None`` and
        raises nothing: the whole-project projection always exists.
        """
        return self._adapter.get_graph(project)
