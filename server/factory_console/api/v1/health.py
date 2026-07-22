"""The ``GET /api/v1/health`` liveness probe: ``{ok, version, projectRoot}``.

Relocated from the walking-skeleton inline router in ``app.py`` (T06) into its own
sub-router, mirroring ``api/v1/project.py``, and enriched to report the resolved
``projectRoot`` ``create_app`` stashed on ``app.state``. The handler reads
``app.state`` directly — it needs no ``FileAdapter`` — and treats ``project_root``
defensively so an app whose root is unbound reports ``projectRoot: null`` rather
than raising.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag (mirrors ``api/v1/project.py``).
router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Liveness payload: ``ok`` plus the running ``version`` and resolved ``projectRoot``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    version: str
    projectRoot: str | None


@router.get("/health")
async def get_health(request: Request) -> HealthResponse:
    """Return the liveness probe with the running version and discovered project root.

    Reads ``version`` and ``project_root`` from ``request.app.state`` (both set by
    ``create_app``). ``project_root`` is read defensively with ``getattr`` so an app
    whose root is unbound yields ``projectRoot: null`` instead of raising an
    ``AttributeError``; a bound ``Path`` is stringified like ``Project.rootPath``.
    """
    version: str = request.app.state.version
    root = getattr(request.app.state, "project_root", None)
    return HealthResponse(ok=True, version=version, projectRoot=str(root) if root else None)
