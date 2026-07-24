"""The ``GET /api/v1/roadmap`` endpoint: the project's rendered ``ROADMAP.md``.

Serves the full roadmap document — the rendered body (``bodyMarkdown`` +
``bodyHtml``) plus the structured ``milestones[]`` parsed from it — for the
discovered project, or the slim ``{present: false}`` envelope when the project
has no roadmap. Mirrors ``api/v1/project.py``: the package ``__init__`` owns the
``/api/v1`` prefix; this sub-router only names the route, its OpenAPI tag, and the
backend-owned :class:`RoadmapAbsent` envelope.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from factory_console.api.deps import get_file_adapter
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
    request: Request,
    adapter: FileAdapter = Depends(get_file_adapter),
) -> Roadmap | RoadmapAbsent:
    """Return the project's full :class:`Roadmap`, or :class:`RoadmapAbsent`.

    Loads the discovered project from ``request.app.state.project_root`` and calls
    ``adapter.get_roadmap(project)``, returning the full :class:`Roadmap` — its
    ``bodyMarkdown``, ``bodyHtml``, and structured ``milestones[]`` — when the
    project has one, else :class:`RoadmapAbsent`. Does no error handling of its
    own: a ``ProjectNotFound`` from ``load_project`` or a ``RoadmapUnreadable``
    (500) from ``adapter.get_roadmap`` propagates to the registered domain-error
    handler, which renders the mapped envelope.
    """
    root: Path = request.app.state.project_root
    project = adapter.load_project(root)
    roadmap = adapter.get_roadmap(project)
    if roadmap is None:
        return RoadmapAbsent()
    return roadmap
