"""The ``GET /api/v1/health`` liveness probe, enriched with the bound ``projectRoot``.

Relocated out of ``app.py`` (where the walking skeleton served it inline) into its
own v1 sub-router, mirroring ``api/v1/project.py``/``api/v1/tickets.py``: the
package ``__init__`` owns the ``/api/v1`` prefix, this module only names the route,
its OpenAPI tag, and the typed response envelope. The body pins ``version`` and the
resolved ``projectRoot`` to what ``create_app`` stashed on ``app.state`` at boot,
so the probe reports the exact target project the console is bound to.
"""

from __future__ import annotations

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
    projectRoot: str | None


@router.get("/health")
async def get_health(request: Request) -> HealthResponse:
    """Return the liveness probe with the resolved ``projectRoot`` from ``app.state``.

    Reads ``version`` and ``project_root`` that ``create_app`` stashed on
    ``app.state`` at boot. ``project_root`` is read defensively via
    ``getattr(..., None)`` (mirroring ``api/deps.py``) so the ``projectRoot: null``
    branch stays reachable when the root is unbound; a bound root serializes as
    ``str(root)``.
    """
    root = getattr(request.app.state, "project_root", None)
    return HealthResponse(
        ok=True,
        version=request.app.state.version,
        projectRoot=str(root) if root else None,
    )
