"""The ``GET /api/v1/project`` endpoint: the SELECTED target :class:`Project`.

First real (non-health) v1 endpoint — landing it froze the OpenAPI schema enough
for the frontend to run ``openapi-typescript``. The handler resolves the root of the
project THIS request is about through
:func:`~factory_console.api.deps.get_current_project_root`, loads it through the
injected :class:`~factory_console.file_adapter.protocol.FileAdapter`, and returns the
resolved :class:`~factory_console.domain.project.Project`.

The root is the SELECTED project's, not the one ``create_app`` pinned at boot. In
pinned mode (``factory-console PATH``, every pre-v3 app) the two are the same path, so
the answer is unchanged; once a project is selected the endpoint follows the selection.

It does no error handling of its own, and gains none here: a
:class:`~factory_console.file_adapter.discovery.ProjectNotFound` from ``load_project``
and the selection seam's own 409s
(:class:`~factory_console.services.project_selection.NoProjectSelected`,
:class:`~factory_console.services.project_selection.SelectedProjectNotRegistered`,
:class:`~factory_console.services.project_selection.SelectedProjectUnavailable`) all
propagate to the domain-error handler ``create_app`` already registers, which renders
each at the status it declares.

``load_project`` stats a tree, so it is awaited through ``anyio.to_thread.run_sync``
rather than run inline on the event loop — ``ARCHITECTURE.md``'s Cross-cutting
**Concurrency** rule, applied per endpoint as it is touched.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import anyio.to_thread
from fastapi import APIRouter, Depends

from factory_console.api.deps import get_current_project_root, get_file_adapter
from factory_console.domain import Project
from factory_console.file_adapter.protocol import FileAdapter

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag.
router = APIRouter(tags=["project"])


@router.get("/project")
async def get_project(
    adapter: FileAdapter = Depends(get_file_adapter),
    root: Path = Depends(get_current_project_root),
) -> Project:
    """Return the SELECTED target :class:`Project` for the running console.

    ``root`` is resolved per request by
    :func:`~factory_console.api.deps.get_current_project_root`, so the project
    described is whichever one is currently selected. Raises nothing itself: a
    ``ProjectNotFound`` from the adapter maps to the 404 envelope and a selection
    failure to its ``409`` — both through the registered domain-error handler, so
    there is no error handling in this endpoint.
    """
    return await anyio.to_thread.run_sync(partial(adapter.load_project, root))
