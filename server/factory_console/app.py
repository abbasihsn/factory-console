"""FastAPI application factory for Factory Console.

Builds the real, bootable app the CLI (T25) launches and every endpoint ticket
extends. :func:`create_app` takes an injected
:class:`~factory_console.file_adapter.protocol.FileAdapter` and stashes it — with
the discovered ``project_root`` and the package ``version`` — on ``app.state`` so
handlers reach the adapter through the ``Depends(get_file_adapter)`` seam without
importing a concrete adapter. It wires the two cross-cutting concerns every
endpoint relies on: the domain/validation exception handlers
(:func:`~factory_console.api.error_handlers.register_error_handlers`) and a single
access-log line per request (:class:`AccessLogMiddleware`). The packaged SPA is
served last, unchanged from the walking skeleton.

:func:`create_dev_app` is the zero-arg factory ``scripts/dev.sh``'s
``uvicorn --factory`` invocation targets; it discovers the project root and
instantiates the filesystem-backed ``RealFileAdapter`` lazily, so importing this
module never imports ``real.py`` (whose only runtime users are this dev shortcut
and T25's production CLI).
"""

from __future__ import annotations

import logging
import time
from importlib import resources
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Scope

from factory_console.api.error_handlers import register_error_handlers
from factory_console.api.v1 import API_V1_PREFIX
from factory_console.api.v1 import router as v1_router
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.logging import request_log_line

# ``API_V1_PREFIX`` (imported above) is owned by the ``api.v1`` package so the
# ``/api/v1`` prefix lives in one place; every v1 endpoint (including the health
# probe at ``/api/v1/health``) hangs off ``v1_router``, and the schema is served at
# ``/api/v1/openapi.json``.

# One access-log record per request is emitted on this named logger, so operators
# (and the tests) can grep/filter request lines independently of application logs.
_ACCESS_LOGGER = logging.getLogger("factory_console.access")


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Emit exactly one ``factory_console.access`` log line per request.

    Times the downstream handler with ``time.perf_counter()`` around
    ``call_next`` and logs AFTER the response is produced (so the status code is
    known), formatting the line with
    :func:`~factory_console.logging.request_log_line`. An UNHANDLED exception from
    ``call_next`` (which ``BaseHTTPMiddleware`` re-raises) becomes a 500 line
    before it propagates, so the one-line-per-request invariant holds even when the
    outer ``ServerErrorMiddleware`` is the one that turns the exception into the
    500 response.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Time the request and emit one access line once the handler returns."""
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            dur_ms = (time.perf_counter() - started) * 1000
            _ACCESS_LOGGER.info(request_log_line(request.method, request.url.path, 500, dur_ms))
            raise
        dur_ms = (time.perf_counter() - started) * 1000
        _ACCESS_LOGGER.info(
            request_log_line(request.method, request.url.path, response.status_code, dur_ms)
        )
        return response


# The ``/api/v1`` prefix as it looks to a ``/``-mounted StaticFiles (leading slash
# stripped): an unknown API endpoint that no router matched must keep its 404 rather
# than fall back to the SPA shell (see ``_SpaStaticFiles``).
_API_STATIC_PREFIX = API_V1_PREFIX.strip("/")


class _SpaStaticFiles(StaticFiles):
    """``StaticFiles`` that falls back to ``index.html`` for SPA client routes.

    The bundled frontend is an ``adapter-static`` SPA in fallback mode
    (``ssr=false`` / ``prerender=false`` — see ``frontend/svelte.config.js``): only
    ``index.html`` and hashed assets exist on disk, and client routes like
    ``/tickets/<id>`` are resolved in the browser, never prerendered. Plain
    ``StaticFiles(html=True)`` serves ``index.html`` only for a *directory* request,
    so a hard refresh, bookmark, browser-open, or shared deep link to a client route
    would 404 with a blank page. Serving ``index.html`` (HTTP 200) on a would-be 404
    hands the path to the SPA router instead.

    The ``/api/v1`` router is registered BEFORE this mount, so a *known* API route is
    matched first and never reaches here. An *unknown* ``/api/v1/*`` path DOES fall
    through (the router adds routes, not a mount), so it is exempted from the
    fallback and keeps its 404 rather than masquerading as the SPA shell.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        """Serve the requested file, or ``index.html`` when a client route would 404."""
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            is_api_path = path == _API_STATIC_PREFIX or path.startswith(f"{_API_STATIC_PREFIX}/")
            if exc.status_code == 404 and not is_api_path:
                return await super().get_response("index.html", scope)
            raise


def _mount_static(app: FastAPI) -> None:
    """Mount the built SPA at ``/`` when ``factory_console/_static/`` exists.

    The SPA bundle is copied into ``_static/`` only at package time (gitignored),
    so a dev checkout ships no bundle and this is a silent no-op — an absent
    ``_static/`` must never break app creation. Uses :class:`_SpaStaticFiles` so a
    deep link / refresh / bookmark to a client route falls back to ``index.html``
    rather than 404ing.
    """
    static_dir = resources.files("factory_console") / "_static"
    if not static_dir.is_dir():
        return
    app.mount("/", _SpaStaticFiles(directory=str(static_dir), html=True), name="static")


def create_app(file_adapter: FileAdapter, *, version: str, project_root: Path) -> FastAPI:
    """Build the Factory Console app around an injected ``FileAdapter``.

    ``file_adapter`` is stashed on ``app.state`` for the
    ``Depends(get_file_adapter)`` seam, alongside ``project_root`` (the discovered
    target project) and ``version``. Registers the domain/validation exception
    handlers and the access-log middleware, includes the v1 router, and mounts the
    packaged SPA last. ``project_root`` is non-optional: the CLI always discovers a
    root before boot and tests always pass a fixture root.
    """
    app = FastAPI(
        title="Factory Console",
        version=version,
        openapi_url=f"{API_V1_PREFIX}/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    app.state.file_adapter = file_adapter
    app.state.project_root = project_root
    app.state.version = version

    register_error_handlers(app)
    app.add_middleware(AccessLogMiddleware)
    app.include_router(v1_router)
    _mount_static(app)
    return app


def create_dev_app() -> FastAPI:
    """Zero-arg app factory targeted by ``scripts/dev.sh``'s ``uvicorn --factory``.

    Discovers the project root from the current working directory and wires the
    filesystem-backed :class:`~factory_console.file_adapter.real.RealFileAdapter`.
    The imports are lazy so importing this module never pulls in ``real.py`` — the
    only runtime users of the real adapter are this dev shortcut and T25's CLI.
    """
    from factory_console import __version__
    from factory_console.file_adapter.discovery import discover_project
    from factory_console.file_adapter.real import RealFileAdapter

    root = discover_project(None, Path.cwd())
    return create_app(RealFileAdapter(), version=__version__, project_root=root)
