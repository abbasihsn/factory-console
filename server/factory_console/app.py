"""Walking-skeleton FastAPI application factory.

Wires the minimal bootable console for the MVP: a single ``GET /api/v1/health``
liveness probe plus optional static-file serving for the built SPA. Deliberately
trivial — it exists to give backend an app to extend, CI a smoke target, and the
frontend a ``/health`` endpoint to hit during dev.

NOTE (next author): backend T20 REWRITES ``create_app`` to take a ``file_adapter``
argument and to register the real v1 routers, exception handlers, and middleware;
backend T24 moves ``/health`` into ``api/v1/health.py``. Keep this module a thin
stub until then.
"""

from importlib import resources

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

import factory_console

# Every v1 route hangs off this prefix, so the health probe is served at
# ``/api/v1/health`` and the schema at ``/api/v1/openapi.json``.
API_V1_PREFIX = "/api/v1"


def _build_v1_router() -> APIRouter:
    """Return the v1 API router carrying the walking-skeleton ``/health`` probe."""
    router = APIRouter(prefix=API_V1_PREFIX)

    @router.get("/health")
    def health() -> dict[str, object]:
        """Liveness probe: ``{ok, version, projectRoot}`` (projectRoot null until T24)."""
        return {
            "ok": True,
            "version": factory_console.__version__,
            "projectRoot": None,
        }

    return router


def _mount_static(app: FastAPI) -> None:
    """Mount the built SPA at ``/`` when ``factory_console/_static/`` exists.

    The SPA bundle is copied into ``_static/`` only at package time (gitignored),
    so a dev checkout ships no bundle and this is a silent no-op — an absent
    ``_static/`` must never break app creation.
    """
    static_dir = resources.files("factory_console") / "_static"
    if not static_dir.is_dir():
        return
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


def create_app() -> FastAPI:
    """Build and return the walking-skeleton Factory Console app."""
    app = FastAPI(
        title="Factory Console",
        version=factory_console.__version__,
        openapi_url=f"{API_V1_PREFIX}/openapi.json",
        docs_url=f"{API_V1_PREFIX}/docs",
    )
    app.include_router(_build_v1_router())
    _mount_static(app)
    return app
