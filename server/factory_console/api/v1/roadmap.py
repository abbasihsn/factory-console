"""The ``GET /api/v1/roadmap`` presence probe for the project ``ROADMAP.md``.

Presence-only in the MVP: the endpoint reports whether the discovered project has a
roadmap document and, when present, its resolved path — never the rendered body
(the ``bodyMarkdown``/``bodyHtml`` on the
:class:`~factory_console.domain.deps.Roadmap` domain model land in a later
milestone). Mirrors ``api/v1/project.py``: the package ``__init__`` owns the
``/api/v1`` prefix; this sub-router only names the route, its OpenAPI tag, and the
slim presence envelopes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from factory_console.api.deps import get_file_adapter
from factory_console.file_adapter.protocol import FileAdapter

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag (mirrors ``api/v1/project.py``).
router = APIRouter(tags=["roadmap"])


class RoadmapPresent(BaseModel):
    """Presence response when the project has a roadmap: ``present: true`` and its ``path``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    present: Literal[True] = True
    path: Path


class RoadmapAbsent(BaseModel):
    """Presence response when the project has no roadmap: ``present: false``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    present: Literal[False] = False


@router.get("/roadmap")
async def get_roadmap(
    request: Request,
    adapter: FileAdapter = Depends(get_file_adapter),
) -> RoadmapPresent | RoadmapAbsent:
    """Return whether the discovered project has a roadmap, and its path when present.

    Loads the discovered project from ``request.app.state.project_root`` and reads
    presence straight off ``project.roadmapPath`` (``None`` exactly when discovery
    found no roadmap): returns :class:`RoadmapPresent` with that resolved path, else
    :class:`RoadmapAbsent`. It deliberately does NOT call ``adapter.get_roadmap``,
    which reads and renders the whole document — wasteful when the MVP serves only
    presence and the path (the rendered body lands with a later milestone). Does no
    error handling of its own: a ``ProjectNotFound`` from ``load_project`` propagates
    to the registered domain-error handler.
    """
    root: Path = request.app.state.project_root
    project = adapter.load_project(root)
    if project.roadmapPath is None:
        return RoadmapAbsent()
    return RoadmapPresent(path=project.roadmapPath)
