"""Version-1 API router aggregation point.

The single place every v1 endpoint is stitched onto the ``/api/v1`` prefix: this
package owns :data:`API_V1_PREFIX` and the aggregation :data:`router`, and each
endpoint ticket extends the seam by appending one ``router.include_router(...)``
line for its sub-router. ``create_app`` includes this aggregated router and reads
:data:`API_V1_PREFIX` for the health route and the OpenAPI url, so the prefix
string lives in exactly one place.
"""

from __future__ import annotations

from fastapi import APIRouter

from factory_console.api.v1.events import router as events_router
from factory_console.api.v1.graph import router as graph_router
from factory_console.api.v1.health import router as health_router
from factory_console.api.v1.project import router as project_router
from factory_console.api.v1.roadmap import router as roadmap_router
from factory_console.api.v1.runs import router as runs_router
from factory_console.api.v1.search import router as search_router
from factory_console.api.v1.spend import router as spend_router
from factory_console.api.v1.tickets import router as tickets_router
from factory_console.api.v1.tickets_write import router as tickets_write_router

# Every v1 route hangs off this prefix, so the schema is served at
# ``/api/v1/openapi.json`` and each endpoint (e.g. ``/api/v1/project``) is prefixed
# once, here, rather than in every sub-router.
API_V1_PREFIX = "/api/v1"

router = APIRouter(prefix=API_V1_PREFIX)
router.include_router(project_router)
router.include_router(tickets_router)
router.include_router(health_router)
router.include_router(roadmap_router)
router.include_router(search_router)
router.include_router(graph_router)
router.include_router(events_router)
router.include_router(tickets_write_router)
router.include_router(spend_router)
router.include_router(runs_router)

__all__ = ["API_V1_PREFIX", "router"]
