"""FastAPI application factory for Factory Console.

Builds the real, bootable app the CLI (T25) launches and every endpoint ticket
extends. :func:`create_app` takes an injected
:class:`~factory_console.file_adapter.protocol.FileAdapter` and stashes it — with
the discovered ``project_root`` and the package ``version`` — on ``app.state`` so
handlers reach the adapter through the ``Depends(get_file_adapter)`` seam without
importing a concrete adapter. It also accepts an optional long-lived
:class:`~factory_console.file_adapter.watcher.FileWatcher` (T39's port), stashed on
``app.state.file_watcher`` for the ``Depends(get_file_watcher)`` seam and driven by
a FastAPI ``lifespan`` that ``start()``s it at boot and ``stop()``s it on shutdown
— the first (deliberate) deviation from the MVP's no-watcher rule, plumbing the
watcher backbone the SSE endpoint (T45) builds on. It also accepts an optional
write-core :class:`~factory_console.file_adapter.writer_protocol.FileWriter` (T60/T61's
port), stashed on ``app.state.file_writer`` for the ``Depends(get_file_writer)`` seam
the v2 write endpoints consume; the writer is stateless, so it drives no lifespan.
It also accepts an optional
:class:`~factory_console.file_adapter.run_artifacts.RunArtifactReader` (T88/T89's
per-ticket artifact port), stashed on ``app.state.run_artifact_reader`` for the
``Depends(get_run_artifact_reader)`` seam the runs endpoint consumes; it too is
stateless and drives no lifespan. It mints the per-session write token (T64) — the
defence-in-depth secret every v2
mutation must present in the
:data:`~factory_console.config.WRITE_TOKEN_HEADER` header — stashing it on
``app.state.write_token`` for
:func:`~factory_console.api.write_token.require_write_token` and announcing it on
stderr for the human operator. It wires the two cross-cutting
concerns every endpoint relies on: the domain/validation exception handlers
(:func:`~factory_console.api.error_handlers.register_error_handlers`) and a single
access-log line per request (:class:`AccessLogMiddleware`). The packaged SPA is
served last, unchanged from the walking skeleton.

:func:`create_dev_app` is the zero-arg factory ``scripts/dev.sh``'s
``uvicorn --factory`` invocation targets; it discovers the project root and
instantiates the filesystem-backed ``RealFileAdapter``, the watchdog-backed
``RealFileWatcher``, the ``RealFileWriter``, and the ``RealRunArtifactReader``
lazily, so importing this module never imports ``real.py``, ``watcher_real.py``, or
``real_writer.py`` (and never pulls in ``watchdog``) — their only runtime users are
this dev shortcut and T25's production CLI.
"""

from __future__ import annotations

import contextlib
import logging
import secrets
import sys
import time
from collections.abc import AsyncIterator
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
from factory_console.api.write_token import publish_write_token_scheme
from factory_console.config import WRITE_TOKEN_HEADER
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.file_adapter.run_artifacts import RunArtifactReader
from factory_console.file_adapter.watcher import FileWatcher
from factory_console.file_adapter.writer_protocol import FileWriter
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


@contextlib.asynccontextmanager
async def _watcher_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start/stop the bound :class:`FileWatcher` around the app's serving window.

    Reads the watcher off ``app.state.file_watcher`` (set by :func:`create_app`)
    rather than closing over the argument, so there is no construct-vs-startup
    ordering hazard. When a watcher is bound, its SYNCHRONOUS ``start()`` runs on
    entry — from inside this async context so ``RealFileWatcher`` captures the
    running loop it later hands watchdog callbacks to — and its ``stop()`` runs in
    a ``finally`` on exit, so uvicorn's SIGINT/SIGTERM drain always joins the
    observer thread even if serving raised, leaving no thread/observer leak. A
    ``None`` watcher (the common test/adapter-only path) makes both a no-op.
    """
    file_watcher: FileWatcher | None = getattr(app.state, "file_watcher", None)
    if file_watcher is not None:
        file_watcher.start()
    try:
        yield
    finally:
        if file_watcher is not None:
            file_watcher.stop()


# Entropy (in bytes) of a generated write token. 32 bytes is the ``secrets`` module's
# own recommendation for a value that must resist brute force, and yields a ~43-char
# URL-safe string — short enough for an operator to copy out of the terminal.
_WRITE_TOKEN_BYTES = 32


def _announce_write_token(token: str, *, generated: bool) -> None:
    """Tell the human operator how to authenticate writes this session.

    Deliberately a ``print`` to ``sys.stderr`` rather than a log call: the token is
    a secret, and the write-path NFR is that it never reaches a log line, so it must
    NOT flow through the configured logging handlers (files, aggregation, the access
    log). stderr also keeps it off stdout, whose only content is the CLI's
    machine-parsable contract line.

    ``generated`` selects what is worth saying, because the two cases differ in both
    truth and need. A generated token exists nowhere else, so it is printed and the
    operator is told it will not survive a restart. A *pinned* one they already have
    (they set ``FACTORY_CONSOLE_WRITE_TOKEN``), so the value is withheld: echoing it
    would say nothing new while writing their long-lived secret into whatever
    captures stderr — a supervisor or CI log file, i.e. exactly the persistence
    keeping this off the logging handlers is meant to avoid.
    """
    if generated:
        print(f"{WRITE_TOKEN_HEADER}: {token}", file=sys.stderr)
        print(
            "  send this header on write requests; a new token is minted at every start",
            file=sys.stderr,
        )
        return
    print(f"{WRITE_TOKEN_HEADER}: <pinned, not echoed>", file=sys.stderr)
    print(
        "  send this header on write requests; the value is the one you pinned in "
        "FACTORY_CONSOLE_WRITE_TOKEN",
        file=sys.stderr,
    )


def create_app(
    file_adapter: FileAdapter,
    *,
    version: str,
    project_root: Path,
    file_watcher: FileWatcher | None = None,
    file_writer: FileWriter | None = None,
    run_artifact_reader: RunArtifactReader | None = None,
    write_token: str | None = None,
) -> FastAPI:
    """Build the Factory Console app around an injected ``FileAdapter``.

    ``file_adapter`` is stashed on ``app.state`` for the
    ``Depends(get_file_adapter)`` seam, alongside ``project_root`` (the discovered
    target project) and ``version``. The optional ``file_watcher`` (T39's
    :class:`FileWatcher` port) is stashed on ``app.state.file_watcher`` for the
    ``Depends(get_file_watcher)`` seam and driven by :func:`_watcher_lifespan`,
    which ``start()``s it at boot and ``stop()``s it on shutdown; leaving it
    ``None`` keeps the app watcher-free (the adapter-only default). The optional
    write-core ``file_writer`` (T60/T61's :class:`FileWriter` port) is stashed on
    ``app.state.file_writer`` for the ``Depends(get_file_writer)`` seam; it is
    stateless, so it drives no lifespan, and leaving it ``None`` keeps the app
    write-free until a write route asks for it (then a missing writer is a wiring
    bug the seam raises on). The optional ``run_artifact_reader`` (T88/T89's
    :class:`RunArtifactReader` port) is stashed on ``app.state.run_artifact_reader``
    for the ``Depends(get_run_artifact_reader)`` seam ``GET /api/v1/runs`` consumes;
    it is stateless, so it drives no lifespan, and leaving it ``None`` is the same
    trade as the writer — every other route keeps working, and the runs route reports
    the wiring bug rather than inventing an answer about the factory.

    ``write_token`` pins the per-session write secret every v2 mutation must present
    in the :data:`~factory_console.config.WRITE_TOKEN_HEADER` header (an operator
    override from ``FACTORY_CONSOLE_WRITE_TOKEN``, or a fixed value in tests);
    leaving it ``None`` — the normal case — mints a fresh random one, so the token
    never outlives the process. Either way it is stashed on
    ``app.state.write_token`` for
    :func:`~factory_console.api.write_token.require_write_token` and announced on
    stderr (the value itself only when it was generated — a pin the operator already
    holds is not echoed), and its ``apiKey`` security scheme is published in the
    OpenAPI document so the contract describes the header. Read routes are untouched:
    nothing here attaches a global dependency, so viewing the project needs no header.

    Registers the domain/validation exception handlers and
    the access-log middleware, includes the v1 router, and mounts the packaged SPA
    last. ``project_root`` is non-optional: the CLI always discovers a root before
    boot and tests always pass a fixture root.
    """
    app = FastAPI(
        title="Factory Console",
        version=version,
        openapi_url=f"{API_V1_PREFIX}/openapi.json",
        docs_url=None,
        redoc_url=None,
        lifespan=_watcher_lifespan,
    )
    app.state.file_adapter = file_adapter
    app.state.project_root = project_root
    app.state.version = version
    app.state.file_watcher = file_watcher
    app.state.file_writer = file_writer
    app.state.run_artifact_reader = run_artifact_reader
    token = write_token or secrets.token_urlsafe(_WRITE_TOKEN_BYTES)
    app.state.write_token = token
    # ``generated`` mirrors the ``or`` above exactly rather than testing ``is None``:
    # any falsy pin takes the generate branch, and the announcement must then print
    # the value — claiming "pinned" would withhold a token the operator has no copy of.
    _announce_write_token(token, generated=not write_token)

    register_error_handlers(app)
    publish_write_token_scheme(app)
    app.add_middleware(AccessLogMiddleware)
    app.include_router(v1_router)
    _mount_static(app)
    return app


def create_dev_app() -> FastAPI:
    """Zero-arg app factory targeted by ``scripts/dev.sh``'s ``uvicorn --factory``.

    Discovers the project root from the current working directory and wires the
    filesystem-backed :class:`~factory_console.file_adapter.real.RealFileAdapter`
    plus the watchdog-backed
    :class:`~factory_console.file_adapter.watcher_real.RealFileWatcher` rooted at
    that same root and the filesystem-backed
    :class:`~factory_console.file_adapter.real_writer.RealFileWriter`, plus the
    filesystem-backed
    :class:`~factory_console.file_adapter.run_artifacts.RealRunArtifactReader` the
    runs endpoint reads the per-ticket lane artifacts through. The imports are lazy
    so importing this module never pulls in ``real.py``, ``watcher_real.py``, or
    ``real_writer.py`` (and never imports ``watchdog``) — the only runtime users of
    the concrete adapter/watcher/writer are this dev shortcut and T25's CLI. The
    artifact reader is imported the same way for symmetry rather than out of
    necessity: it shares :mod:`~factory_console.file_adapter.run_artifacts` with the
    port this module already imports for its signature, so nothing new arrives with
    it.

    The write token comes from ``FACTORY_CONSOLE_WRITE_TOKEN`` via
    :func:`~factory_console.config.read_write_token` so a dev loop can pin it across
    reloads (uvicorn's reloader re-runs this factory, which would otherwise mint a new
    token and invalidate the one in the operator's clipboard); unset, ``create_app``
    generates one per boot. Reading it through that helper rather than a bare
    ``Settings()`` matters here: this factory never chooses a bind host (``dev.sh``
    passes ``--host`` to uvicorn itself), so a non-loopback ``FACTORY_CONSOLE_HOST``
    left in the developer's shell must not be validated — and kill the dev server —
    on the way to fetching a token.
    """
    from factory_console import __version__
    from factory_console.config import read_write_token
    from factory_console.file_adapter.discovery import discover_project
    from factory_console.file_adapter.real import RealFileAdapter
    from factory_console.file_adapter.real_writer import RealFileWriter
    from factory_console.file_adapter.run_artifacts import RealRunArtifactReader
    from factory_console.file_adapter.watcher_real import RealFileWatcher

    # Same exit-2-style handling the CLI gives this variable. A bare ValueError here
    # would surface as an unhandled traceback out of uvicorn's factory loader — and
    # because dev.sh runs with ``--reload``, the factory re-runs on every save, so a
    # too-short pin left in the shell would crash-loop the dev server instead of
    # failing once with the message that names the fix.
    try:
        write_token = read_write_token()
    except ValueError as exc:
        raise SystemExit(f"{exc}\nSet a valid FACTORY_CONSOLE_WRITE_TOKEN or unset it.") from exc

    root = discover_project(None, Path.cwd())
    return create_app(
        RealFileAdapter(),
        version=__version__,
        project_root=root,
        file_watcher=RealFileWatcher(root),
        file_writer=RealFileWriter(),
        run_artifact_reader=RealRunArtifactReader(),
        write_token=write_token,
    )
