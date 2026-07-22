"""The ``GET /api/v1/roadmap`` presence probe: ``{present: true, path} | {present: false}``.

The MVP returns presence only — whether the target project ships a ``ROADMAP.md``
and, if so, where — not the rendered body (that lands in a later milestone). The
handler loads the discovered project through the injected ``FileAdapter`` and asks
it for the roadmap, collapsing the full
:class:`~factory_console.domain.deps.Roadmap` to a small presence envelope: an
absent roadmap becomes :class:`RoadmapAbsent`, a present one :class:`RoadmapPresent`
carrying its resolved ``path``.

Like ``api/v1/project.py`` it does no error handling of its own — a
:class:`~factory_console.file_adapter.discovery.ProjectNotFound` raised by
``load_project`` propagates to the domain-error handler ``create_app`` registers,
which renders the 404 envelope.
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
    """Presence envelope for a project that ships a roadmap: its resolved ``path``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    present: Literal[True] = True
    path: Path


class RoadmapAbsent(BaseModel):
    """Presence envelope for a project with no roadmap."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    present: Literal[False] = False


@router.get("/roadmap")
async def get_roadmap(
    request: Request,
    adapter: FileAdapter = Depends(get_file_adapter),
) -> RoadmapPresent | RoadmapAbsent:
    """Return whether the discovered project ships a roadmap, and where if so.

    Loads the project from ``request.app.state.project_root`` through the injected
    adapter and asks it for the roadmap. Returns :class:`RoadmapAbsent` when the
    project has none, else :class:`RoadmapPresent` carrying the roadmap ``path``
    (serialized to a string, like ``Project.rootPath``). Raises nothing itself: a
    ``ProjectNotFound`` from ``load_project`` propagates to the registered
    domain-error handler.
    """
    root: Path = request.app.state.project_root
    project = adapter.load_project(root)
    roadmap = adapter.get_roadmap(project)
    if roadmap is None:
        return RoadmapAbsent()
    return RoadmapPresent(path=roadmap.path)
