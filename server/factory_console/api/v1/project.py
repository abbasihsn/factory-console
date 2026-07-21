"""The ``GET /api/v1/project`` endpoint: the discovered target :class:`Project`.

First real (non-health) v1 endpoint — landing it freezes the OpenAPI schema enough
for the frontend to run ``openapi-typescript``. The handler reads the discovered
project root that ``create_app`` stashed on ``app.state.project_root`` (a ``Path``
guaranteed present at boot), loads the target project through the injected
:class:`~factory_console.file_adapter.protocol.FileAdapter`, and returns the
resolved :class:`~factory_console.domain.project.Project`. It does no error handling
of its own: a :class:`~factory_console.file_adapter.discovery.ProjectNotFound` raised
by ``load_project`` propagates to the domain-error handler ``create_app`` registers,
which renders the 404 envelope.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request

from factory_console.api.deps import get_file_adapter
from factory_console.domain import Project
from factory_console.file_adapter.protocol import FileAdapter

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag.
router = APIRouter(tags=["project"])


@router.get("/project")
async def get_project(
    request: Request,
    adapter: FileAdapter = Depends(get_file_adapter),
) -> Project:
    """Return the discovered target :class:`Project` for the running console.

    Reads the discovered root from ``request.app.state.project_root`` — a ``Path``
    ``create_app`` requires at boot — and returns ``adapter.load_project(root)``.
    Raises nothing itself: a ``ProjectNotFound`` from the adapter propagates to the
    registered domain-error handler, which maps it to the 404 envelope.
    """
    root: Path = request.app.state.project_root
    return adapter.load_project(root)
