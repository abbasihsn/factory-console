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
from pydantic import ConfigDict

from factory_console.api.deps import get_current_project_root, get_file_adapter
from factory_console.domain import Project
from factory_console.domain.subversion import Subversion
from factory_console.file_adapter.protocol import FileAdapter

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag.
router = APIRouter(tags=["project"])


class ProjectView(Project):
    """The resolved :class:`Project`, plus the factory state a human watches for.

    **Subclassing rather than composing, for two reasons.** On the wire it keeps every
    existing key exactly where it was and adds one — so this endpoint gains a field
    instead of changing shape, and nothing reading ``project.rootPath`` has to move.
    In the code it keeps :class:`Project` itself honest: that model documents itself as
    carrying "only resolved paths — never file contents", and it is constructed once per
    request and passed to every adapter method. ``subversion`` is CONTENT, read out of
    ``run-state.json``, so putting it there would make a request-scoped path bundle
    depend on a file's contents and quietly invalidate that promise for every caller.

    The addition lives at the API layer because that is the only layer that wants it: no
    adapter method takes a ``ProjectView``, and none should.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subversion: Subversion | None = None


@router.get("/project")
async def get_project(
    adapter: FileAdapter = Depends(get_file_adapter),
    root: Path = Depends(get_current_project_root),
) -> ProjectView:
    """Return the SELECTED target project, with the open sub-version if there is one.

    ``root`` is resolved per request by
    :func:`~factory_console.api.deps.get_current_project_root`, so the project
    described is whichever one is currently selected. Raises nothing itself: a
    ``ProjectNotFound`` from the adapter maps to the 404 envelope and a selection
    failure to its ``409`` — both through the registered domain-error handler, so
    there is no error handling in this endpoint.

    ``subversion`` is v3's one recurring human gate — the factory accumulates a
    sub-version's tickets onto one branch and then HOLDS at that branch's PR, waiting.
    ``None`` is the normal state between cuts and must render as nothing at all.

    Both reads happen in ONE ``run_sync``. They describe the same project at the same
    moment, and splitting them would let the second observe a run-state the first did
    not — a page naming a sub-version for a project the first call had already resolved
    differently.
    """
    return await anyio.to_thread.run_sync(partial(_load, adapter, root))


def _load(adapter: FileAdapter, root: Path) -> ProjectView:
    """Load the project and its open sub-version, off the event loop.

    A named function rather than a lambda inside ``run_sync`` because both calls are
    blocking filesystem work and must be on the SAME side of the thread hop; a version
    that awaited twice would put a scheduling point between two reads of one project.
    """
    project = adapter.load_project(root)
    return ProjectView(
        **project.model_dump(),
        subversion=adapter.read_subversion(project),
    )
