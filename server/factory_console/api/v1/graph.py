"""The ``GET /api/v1/graph`` endpoint: the whole-project dependency DAG.

Serves the one payload the v1 ``/graph`` route's Cytoscape render needs — every
ticket a node (carrying its resolved run-state for colouring), every RESOLVED
``dependsOn`` an edge — so the frontend fetches the whole graph once rather than
walking per-ticket ``deps``. The handler resolves the root of the project THIS
request is about through
:func:`~factory_console.api.deps.get_current_project_root`, loads it through the
injected :class:`~factory_console.file_adapter.protocol.FileAdapter`, and returns the
:class:`~factory_console.domain.graph.TicketGraph` the
:class:`~factory_console.services.graph_service.GraphService` resolves.

The root is the SELECTED project's, not the one ``create_app`` pinned at boot; in
pinned mode the two are the same path, so the graph is unchanged there.

It does no error handling of its own, and gains none here: a
:class:`~factory_console.file_adapter.discovery.ProjectNotFound` from ``load_project``
and the selection seam's 409s alike propagate to the domain-error handler
``create_app`` registers, which renders each at the status it declares.

Both the load and the graph resolution walk the ticket tree, so both are awaited
through ``anyio.to_thread.run_sync`` rather than run inline on the event loop —
``ARCHITECTURE.md``'s Cross-cutting **Concurrency** rule, applied per endpoint as it
is touched.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import anyio.to_thread
from fastapi import APIRouter, Depends

from factory_console.api.deps import get_current_project_root, get_file_adapter
from factory_console.domain.graph import TicketGraph
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.services.graph_service import GraphService

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag.
router = APIRouter(tags=["graph"])


@router.get("/graph")
async def get_graph(
    adapter: FileAdapter = Depends(get_file_adapter),
    root: Path = Depends(get_current_project_root),
) -> TicketGraph:
    """Return the SELECTED project's whole-project dependency :class:`TicketGraph`.

    ``root`` is resolved per request by
    :func:`~factory_console.api.deps.get_current_project_root`; the project is then
    loaded and handed to :class:`GraphService`. Returns the ``TicketGraph`` domain
    model directly so OpenAPI publishes its nodes+edges shape. Raises nothing itself:
    a ``ProjectNotFound`` from the adapter maps to the 404 envelope and a selection
    failure to its ``409``, both through the registered domain-error handler.

    ``functools.partial`` binds the arguments for both offloads because ``run_sync``
    passes positionals only and takes no keywords.
    """
    project = await anyio.to_thread.run_sync(partial(adapter.load_project, root))
    return await anyio.to_thread.run_sync(partial(GraphService(adapter).get_graph, project))
