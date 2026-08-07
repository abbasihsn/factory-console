"""FastAPI application factory for Factory Console.

Builds the real, bootable app the CLI (T25) launches and every endpoint ticket
extends. :func:`create_app` takes an injected
:class:`~factory_console.file_adapter.protocol.FileAdapter` and stashes it — with
the discovered ``project_root`` and the package ``version`` — on ``app.state`` so
handlers reach the adapter through the ``Depends(get_file_adapter)`` seam without
importing a concrete adapter. It also accepts an optional long-lived
:class:`~factory_console.file_adapter.watcher.FileWatcher` (T39's port) — the first
(deliberate) deviation from the MVP's no-watcher rule, plumbing the watcher backbone
the SSE endpoint (T45) builds on — which it hands to the
:class:`~factory_console.services.watcher_supervisor.WatcherSupervisor` it ALWAYS
builds on ``app.state.watcher_supervisor``. The supervisor is what the
``Depends(get_file_watcher)`` seam now reads through, what the FastAPI ``lifespan``
``start()``s at boot and ``stop()``s on shutdown, and what re-points the watcher at
the newly selected project when the selection changes. It also accepts an optional
write-core :class:`~factory_console.file_adapter.writer_protocol.FileWriter` (T60/T61's
port), stashed on ``app.state.file_writer`` for the ``Depends(get_file_writer)`` seam
the v2 write endpoints consume; the writer is stateless, so it drives no lifespan.
It also accepts an optional
:class:`~factory_console.file_adapter.run_artifacts.RunArtifactReader` (T88/T89's
per-ticket artifact port), stashed on ``app.state.run_artifact_reader`` for the
``Depends(get_run_artifact_reader)`` seam the runs endpoint consumes; it too is
stateless and drives no lifespan. It also accepts an optional
:class:`~factory_console.store.registry_protocol.ProjectRegistry` (T106's port over the
console's OWN state), stashed on ``app.state.project_registry`` for the
``Depends(get_project_registry)`` seam, and ALWAYS constructs a
:class:`~factory_console.services.project_selection.SelectionState` on
``app.state.selection`` — seeded from ``project_root``, so an app built without a
registry is simply an app that is permanently pinned, which is today's behaviour
exactly. It mints the per-session write token (T64) — the
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
``RealFileWatcher``, and the ``RealFileWriter`` lazily, so importing this module
never imports ``real.py``, ``watcher_real.py``, or ``real_writer.py`` (and never
pulls in ``watchdog``) — their only runtime users are this dev shortcut and T25's
production CLI. It also instantiates the ``RealRunArtifactReader``, imported the
same lazy way for SYMMETRY rather than for isolation: that concrete lives in
``run_artifacts.py``, which this module already imports at module scope for the
``RunArtifactReader`` port in :func:`create_app`'s signature, so — unlike the three
above — deferring it keeps nothing new out of the import graph.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import sys
import time
from collections.abc import AsyncIterator, Callable
from importlib import resources
from pathlib import Path

import anyio.to_thread
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
from factory_console.services.project_selection import SelectionState
from factory_console.services.watcher_supervisor import WatcherSupervisor
from factory_console.store.registry_protocol import ProjectRegistry

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
    """Open/close the :class:`WatcherSupervisor`'s window around the app's serving.

    Reads the supervisor off ``app.state.watcher_supervisor`` (always set by
    :func:`create_app`) rather than closing over an argument, so there is no
    construct-vs-startup ordering hazard. Its SYNCHRONOUS ``start()`` runs on entry —
    from inside this async context so ``RealFileWatcher`` captures the running loop it
    later hands watchdog callbacks to — rooted at ``app.state.project_root``, the
    boot-time root an injected ``file_watcher`` was already built for. ``stop()`` runs
    in a ``finally`` on exit, so uvicorn's SIGINT/SIGTERM drain always joins the
    observer thread even if serving raised, leaving no thread/observer leak; it is
    idempotent and safe with no watcher current, which is what makes it unconditional
    here. A supervisor with neither an injected watcher nor a factory (the common
    test/adapter-only path) makes both ends a no-op.
    """
    supervisor: WatcherSupervisor = app.state.watcher_supervisor
    supervisor.start(app.state.project_root)
    try:
        yield
    finally:
        supervisor.stop()


def _watcher_retarget_hook(supervisor: WatcherSupervisor) -> Callable[[Path | None], None]:
    """Adapt ``supervisor.retarget`` to the selection hook without blocking the loop.

    :meth:`~factory_console.services.project_selection.SelectionState.subscribe`
    invokes its callbacks SYNCHRONOUSLY, on the event-loop thread, from inside
    ``select()`` — which a request handler calls. But a swap releases the outgoing
    watcher, and ``FileWatcher.stop()`` joins a thread. Calling that inline would park
    the single event loop on the join, stalling every other request and open SSE stream
    — exactly the failure ``ARCHITECTURE.md``'s Cross-cutting **Concurrency** rule
    exists to prevent. So the callback returns IMMEDIATELY and the swap runs as a task.

    The task splits the swap by thread requirement, because the two halves have opposite
    ones. Only
    :meth:`~factory_console.services.watcher_supervisor.WatcherSupervisor.retarget_release`
    — the blocking ``stop()`` — is sent through ``anyio.to_thread.run_sync``;
    :meth:`~factory_console.services.watcher_supervisor.WatcherSupervisor.retarget_rebuild`
    runs back ON the loop thread once that await returns, because it calls
    ``FileWatcher.start()`` and a ``RealFileWatcher`` captures the running loop there. Run
    on the worker thread it would find no running loop, raise, and be swallowed into a
    permanently watcher-less console — a real project switch that silently loses live
    updates. ``retarget_release`` returning ``False`` (a same-root re-selection, or a
    supervisor already outside its serving window) skips the rebuild entirely.

    Fire-and-forget, and that is the deliberate trade: the switch is confirmed to the
    operator as soon as the selection is persisted, and the live-update stream catches
    up a moment later (the SSE contract in T115 is what makes the gap safe — a
    connection ends its stream when the generation moves, so a client re-subscribes to
    whichever watcher is current by then). Neither half raises, so there is no result to
    await and nothing for the task to report; the tasks are kept in a set only so the
    loop holds a strong reference and cannot garbage-collect one mid-swap.

    **Swaps are SERIALISED, and the lock is load-bearing.** Fire-and-forget means two
    ``select()`` calls close together (a double-clicked switcher, two tabs) put two
    ``_swap`` tasks in flight, and a swap is not atomic: it releases on a worker thread
    and rebuilds on the loop. Unserialised, both releases run before either rebuild —
    the second finds ``current()`` already ``None`` and so stops nothing, and then both
    rebuilds run, the second overwriting the first's watcher WITHOUT stopping it. That
    orphans a live watchdog observer thread and its recursive watches: shutdown's
    ``stop()`` only ever sees the current watcher, so nothing joins the abandoned one.
    Holding the lock across the whole body makes each swap observe the previous one's
    finished state, so the outgoing watcher of every swap is the one actually released.
    The lock never serialises anything the operator waits on — the hook already returned.
    """
    pending: set[asyncio.Task[None]] = set()
    swapping = asyncio.Lock()

    async def _swap(root: Path | None) -> None:
        """Release the outgoing watcher off the loop, then rebuild back on it.

        Under ``swapping`` for its whole body so an overlapping swap cannot interleave
        its release between this one's two halves and orphan the watcher this one built.
        """
        async with swapping:
            if await anyio.to_thread.run_sync(supervisor.retarget_release, root):
                supervisor.retarget_rebuild(root)

    def _retarget_off_loop(root: Path | None) -> None:
        """Schedule the swap as a loop task; do it inline when there is no loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # A synchronous caller with no loop at all (a test driving ``select()``
            # directly, a future CLI-side switch): there is no event loop for the join
            # to stall and none for a ``RealFileWatcher`` to capture, so the whole swap
            # runs here rather than being deferred onto a loop that does not exist.
            supervisor.retarget(root)
            return
        task = loop.create_task(_swap(root))
        pending.add(task)
        task.add_done_callback(pending.discard)

    return _retarget_off_loop


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
    watcher_factory: Callable[[Path], FileWatcher] | None = None,
    file_writer: FileWriter | None = None,
    run_artifact_reader: RunArtifactReader | None = None,
    project_registry: ProjectRegistry | None = None,
    write_token: str | None = None,
) -> FastAPI:
    """Build the Factory Console app around an injected ``FileAdapter``.

    ``file_adapter`` is stashed on ``app.state`` for the
    ``Depends(get_file_adapter)`` seam, alongside ``project_root`` (the discovered
    target project) and ``version``. The optional ``file_watcher`` (T39's
    :class:`FileWatcher` port) and ``watcher_factory`` (which builds one for a given
    root) are handed to a :class:`WatcherSupervisor` — ALWAYS constructed, on
    ``app.state.watcher_supervisor`` — which owns at most one live watcher, is driven
    by :func:`_watcher_lifespan` (``start()`` at boot, ``stop()`` on shutdown), is read
    through by the ``Depends(get_file_watcher)`` seam, and is re-pointed at the new
    root on every selection change via :func:`_watcher_retarget_hook`. Passing
    ``file_watcher`` alone is exactly the pre-v3 wiring: that instance is the one
    started and served, and with no factory to build a successor the app simply becomes
    watcher-less if the selection ever moves. Passing neither keeps the app
    watcher-free (the adapter-only default). ``app.state.file_watcher`` is
    deliberately NOT bound: the supervisor swaps its watcher mid-session, so a second
    copy of the reference could only go stale and disagree with
    :func:`~factory_console.api.deps.get_file_watcher`. The optional
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

    The optional ``project_registry`` (T106's :class:`ProjectRegistry` port over the
    console's own store) is stashed on ``app.state.project_registry`` for the
    ``Depends(get_project_registry)`` seam, and a
    :class:`~factory_console.services.project_selection.SelectionState` is ALWAYS built
    on ``app.state.selection`` from ``project_root`` and that registry. Leaving the
    registry ``None`` is not a degraded app: it is PINNED MODE — the selection can
    never leave the boot-time root, which is precisely what every pre-v3 app did, and
    is why this milestone can add the seam without changing a single endpoint's
    behaviour. ``app.state.project_root`` keeps naming the PINNED root either way; the
    selection is read per request through ``Depends(get_current_project_root)`` and
    never rewrites it.

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
    app.state.file_writer = file_writer
    app.state.run_artifact_reader = run_artifact_reader
    app.state.project_registry = project_registry
    supervisor = WatcherSupervisor(watcher_factory, initial=file_watcher)
    app.state.watcher_supervisor = supervisor
    selection = SelectionState(pinned_root=project_root, registry=project_registry)
    selection.subscribe(_watcher_retarget_hook(supervisor))
    app.state.selection = selection
    # Serialises the two-phase selection switch the write routes perform. `select()`
    # is atomic, but an HTTP caller cannot use it: its registry round trip blocks and
    # its on-change hook needs the loop, so `api/v1/projects.py` runs
    # `_resolve_and_persist` off-loop and `_apply_selected` back on it. That await is
    # what makes the lock load-bearing — the loop no longer serialises a switch the way
    # it serialises an ordinary handler, so two concurrent switches (or a switch racing
    # a delete-triggered clear) could otherwise persist in one order and apply in the
    # other, leaving the in-memory selection and the watcher pointed at a project the
    # registry no longer names as selected. Same hazard, and the same remedy, as the
    # swap lock in `_watcher_retarget_hook`.
    #
    # It lives HERE, not on SelectionState, because that class is deliberately
    # synchronous and loop-free (a test drives `select()` with no loop at all) and an
    # `asyncio.Lock` attribute would end that. One lock per app, like every other
    # `app.state` singleton.
    app.state.selection_lock = asyncio.Lock()
    # Serialises the ticket write path. `ARCHITECTURE.md`'s Cross-cutting **Concurrency**
    # rule promises a single writer ("the write path is serialized by the same single
    # worker"), and while the handlers ran their blocking work inline the event loop
    # delivered that for free. It no longer does: `api/v1/tickets_write.py` now hands both
    # the project load and the write-service call to `anyio.to_thread.run_sync` (the same
    # rule's other half — no blocking filesystem I/O on the loop), and anyio's default
    # thread limiter admits many of them at once. A ticket write is a read-modify-write of
    # `tickets.json` with no lock below this layer, so two overlapping writes could each
    # render a manifest from the same pre-write bytes and last-write-wins would silently
    # drop one entry — or let two creates of the same id both pass the duplicate guard.
    # This lock is what restores the single-writer-at-a-time invariant across the offload.
    #
    # One lock per app, on `app.state` like every other singleton, and read back through
    # `Depends(get_write_lock)`: a lock built anywhere per-call would satisfy the type and
    # serialise nothing.
    app.state.write_lock = asyncio.Lock()
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
    it. The console's own
    :class:`~factory_console.store.sqlite_registry.SqliteProjectRegistry` is imported
    the same lazy way and wired alongside ``RealFileWatcher`` as the
    ``watcher_factory``, so the dev loop is multi-project exactly as the shipped CLI
    is; a store that cannot be addressed warns on stderr and leaves the app pinned.
    Like the CLI, this factory does NOT register the discovered root — it is an
    ephemeral session pin, so a dev boot never grows the developer's dropdown.

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
    from factory_console.store.sqlite_registry import open_project_registry_or_warn

    # Same exit-2-style handling the CLI gives this variable. A bare ValueError here
    # would surface as an unhandled traceback out of uvicorn's factory loader — and
    # because dev.sh runs with ``--reload``, the factory re-runs on every save, so a
    # too-short pin left in the shell would crash-loop the dev server instead of
    # failing once with the message that names the fix.
    try:
        write_token = read_write_token()
    except ValueError as exc:
        raise SystemExit(f"{exc}\nSet a valid FACTORY_CONSOLE_WRITE_TOKEN or unset it.") from exc

    # The dev loop wires the SAME registry + watcher factory the CLI does, so
    # ``scripts/dev.sh`` exercises multi-project rather than a pinned-only app the
    # shipped binary does not match. Degrading to ``None`` on an unaddressable store
    # matters MORE here than in the CLI: ``--reload`` re-runs this factory on every
    # save, so a raise would crash-loop the dev server. See
    # ``open_project_registry_or_warn`` for the shared degrade-to-pinned policy.
    project_registry = open_project_registry_or_warn(lambda msg: print(msg, file=sys.stderr))

    root = discover_project(None, Path.cwd())
    return create_app(
        RealFileAdapter(),
        version=__version__,
        project_root=root,
        file_watcher=RealFileWatcher(root),
        watcher_factory=RealFileWatcher,
        file_writer=RealFileWriter(),
        run_artifact_reader=RealRunArtifactReader(),
        project_registry=project_registry,
        write_token=write_token,
    )
