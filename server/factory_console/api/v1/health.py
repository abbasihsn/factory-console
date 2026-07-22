"""The ``GET /api/v1/health`` liveness probe, enriched with the bound ``projectRoot``.

Relocated out of ``app.py`` (where the walking skeleton served it inline) into its
own v1 sub-router, mirroring ``api/v1/project.py``/``api/v1/tickets.py``: the
package ``__init__`` owns the ``/api/v1`` prefix, this module only names the route,
its OpenAPI tag, and the typed response envelope. The body pins ``version`` and the
resolved ``projectRoot`` to what ``create_app`` stashed on ``app.state`` at boot,
so the probe reports the exact target project the console is bound to.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag (mirrors ``api/v1/project.py``).
router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Liveness probe body: service ``ok``, its ``version``, and the bound ``projectRoot``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    version: str
    projectRoot: Path


@router.get("/health")
async def get_health(request: Request) -> HealthResponse:
    """Return the liveness probe with the resolved ``projectRoot`` from ``app.state``.

    Reads ``version`` and ``project_root`` that ``create_app`` stashed on
    ``app.state`` at boot and trusts them: ``create_app`` binds a non-optional
    ``project_root`` (the CLI always discovers a root; tests always pass a fixture
    root), so — like its sibling handlers and the ``version`` read beside it — this
    reads the root directly and lets Pydantic serialize the ``Path`` to a string.
    """
    root: Path = request.app.state.project_root
    return HealthResponse(
        ok=True,
        version=request.app.state.version,
        projectRoot=root,
    )
