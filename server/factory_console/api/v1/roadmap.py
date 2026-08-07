"""The ``GET /api/v1/roadmap`` endpoint: the project's rendered ``ROADMAP.md``.

Serves the full roadmap document — the rendered body (``bodyMarkdown`` +
``bodyHtml``) plus the structured ``milestones[]`` parsed from it — for the
SELECTED project, or the slim ``{present: false}`` envelope when that project has no
roadmap. Mirrors ``api/v1/project.py``: the package ``__init__`` owns the
``/api/v1`` prefix; this sub-router only names the route, its OpenAPI tag, and the
backend-owned :class:`RoadmapAbsent` envelope.

The root is the SELECTED project's, resolved per request by
:func:`~factory_console.api.deps.get_current_project_root`, not the one ``create_app``
pinned at boot; in pinned mode the two are the same path. Both filesystem calls are
awaited through ``anyio.to_thread.run_sync`` rather than run inline on the event loop —
``ARCHITECTURE.md``'s Cross-cutting **Concurrency** rule, applied per endpoint as it is
touched.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Literal

import anyio.to_thread
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from factory_console.api.deps import get_current_project_root, get_file_adapter
from factory_console.domain import Roadmap
from factory_console.file_adapter.protocol import FileAdapter

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag (mirrors ``api/v1/project.py``).
router = APIRouter(tags=["roadmap"])


class RoadmapAbsent(BaseModel):
    """Response when the project has no roadmap: ``present: false``.

    The backend-owned discriminator: :class:`~factory_console.domain.deps.Roadmap`
    carries no ``present`` field, so the frontend discriminates the present branch
    from this absent one on that key's presence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    present: Literal[False] = False


@router.get("/roadmap")
async def get_roadmap(
    adapter: FileAdapter = Depends(get_file_adapter),
    root: Path = Depends(get_current_project_root),
) -> Roadmap | RoadmapAbsent:
    """Return the SELECTED project's full :class:`Roadmap`, or :class:`RoadmapAbsent`.

    Loads the project at the per-request ``root`` and calls
    ``adapter.get_roadmap(project)``, returning the full :class:`Roadmap` — its
    ``bodyMarkdown``, ``bodyHtml``, and structured ``milestones[]`` — when the
    project has one, else :class:`RoadmapAbsent`. Does no error handling of its
    own: a ``ProjectNotFound`` from ``load_project``, a ``RoadmapUnreadable``
    (500) from ``adapter.get_roadmap``, and the selection seam's ``409``s all
    propagate to the registered domain-error handler, which renders the mapped
    envelope.
    """
    project = await anyio.to_thread.run_sync(partial(adapter.load_project, root))
    roadmap = await anyio.to_thread.run_sync(partial(adapter.get_roadmap, project))
    if roadmap is None:
        return RoadmapAbsent()
    return roadmap
