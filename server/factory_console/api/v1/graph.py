"""The ``GET /api/v1/graph`` endpoint: the whole-project dependency DAG.

Serves the one payload the v1 ``/graph`` route's Cytoscape render needs — every
ticket a node (carrying its resolved run-state for colouring), every RESOLVED
``dependsOn`` an edge — so the frontend fetches the whole graph once rather than
walking per-ticket ``deps``. The handler reads the discovered project root that
``create_app`` stashed on ``app.state.project_root`` (a ``Path`` guaranteed
present at boot), loads the target project through the injected
:class:`~factory_console.file_adapter.protocol.FileAdapter`, and returns the
:class:`~factory_console.domain.graph.TicketGraph` the
:class:`~factory_console.services.graph_service.GraphService` resolves. It does no
error handling of its own: a
:class:`~factory_console.file_adapter.discovery.ProjectNotFound` raised by
``load_project`` propagates to the domain-error handler ``create_app`` registers,
which renders the 404 envelope.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request

from factory_console.api.deps import get_file_adapter
from factory_console.domain.graph import TicketGraph
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.services.graph_service import GraphService

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag.
router = APIRouter(tags=["graph"])


@router.get("/graph")
async def get_graph(
    request: Request,
    adapter: FileAdapter = Depends(get_file_adapter),
) -> TicketGraph:
    """Return the whole-project dependency :class:`TicketGraph`.

    Reads the discovered root from ``request.app.state.project_root`` — a ``Path``
    ``create_app`` requires at boot — loads the target project, and returns the
    graph resolved by :class:`GraphService`. Returns the ``TicketGraph`` domain
    model directly so OpenAPI publishes its nodes+edges shape. Raises nothing
    itself: a ``ProjectNotFound`` from the adapter propagates to the registered
    domain-error handler, which maps it to the 404 envelope.
    """
    root: Path = request.app.state.project_root
    project = adapter.load_project(root)
    service = GraphService(adapter)
    return service.get_graph(project)
