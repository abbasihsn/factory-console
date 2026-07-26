"""The real ``factory-console`` Typer entrypoint that ships in the wheel.

Extends the T06 walking skeleton into the production launcher and is the ONLY
place in the codebase that constructs the concrete
:class:`~factory_console.file_adapter.real.RealFileAdapter`,
:class:`~factory_console.file_adapter.watcher_real.RealFileWatcher`, and
:class:`~factory_console.file_adapter.real_writer.RealFileWriter` for a
production boot (the dev loop's :func:`~factory_console.app.create_dev_app` is the
other, sole runtime user of all three). The watcher is handed to ``create_app`` and
started/stopped entirely by the app lifespan — this module never touches it
directly. It wires, in a deliberate cheap-input-first order, the full CLI contract
from ``ARCHITECTURE.md``:

* ``--version`` prints ``factory-console v{__version__}`` and exits 0;
* ``--host`` is validated against the 127.0.0.1 loopback trust boundary via the
  shared :func:`~factory_console.config.require_loopback_host` rule (exit 2);
* ``--port`` is range-checked to ``0..65535`` up front (exit 2) so an out-of-range
  value fails like every other bad input instead of reaching ``socket.bind``
  (whose ``OverflowError`` the bind ``except OSError`` would miss);
* ``--log-level`` is normalized via
  :func:`~factory_console.logging.normalize_log_level` (exit 2) before logging is
  configured;
* the project root is discovered with
  :func:`~factory_console.file_adapter.discovery.discover_project` (exit 1 when no
  App Factory project is found);
* the manifest is force-parsed once at boot so a
  :class:`~factory_console.file_adapter.manifest.MalformedManifest` fails fast
  (exit 3) *before* any port is bound or URL printed;
* the port is chosen/confirmed with a throwaway probe socket (an in-use explicit
  ``--port`` exits 2, ``--port 0`` resolves a concrete ephemeral port);
* the exact contract line is printed to stdout, then Uvicorn serves the app.

Signals: this module installs NO hand-rolled signal handlers. ``uvicorn.Server``
captures SIGINT/SIGTERM itself, sets ``should_exit = True``, and drains — so Ctrl-C
(or ``kill``) shuts the console down cleanly and the process exits 0, the ticket's
SIGINT/SIGTERM contract. That drain runs the app lifespan's shutdown, which
``stop()``s the ``RealFileWatcher`` (joining its observer thread), so the watcher
needs no signal handling here. On SIGTERM ``server.run()`` then returns normally;
on Python 3.11+ SIGINT the asyncio runner re-raises ``KeyboardInterrupt`` out of
``server.run()`` *after* that clean shutdown, which :func:`main` catches and
swallows so Ctrl-C still exits 0 rather than 130.

``--host``/``--port``/``--log-level`` also read the documented
``FACTORY_CONSOLE_HOST``/``FACTORY_CONSOLE_PORT``/``FACTORY_CONSOLE_LOG_LEVEL`` env
vars (Typer ``envvar=``), with an explicit flag winning over the env var and the
env var over the default — so the config surface the README advertises is live and
still runs through the same host/log-level/port validation. The write token has no
flag (an argv secret is readable by every local process): ``create_app`` mints a
fresh one per boot and prints it to stderr, and ``FACTORY_CONSOLE_WRITE_TOKEN`` —
read here through :class:`~factory_console.config.Settings` — pins it instead.

Exit codes: ``0`` ok · ``1`` project-not-found · ``2`` bad host / out-of-range port
/ bad log level / port-in-use · ``3`` malformed manifest.
"""

import contextlib
import logging
import socket
import threading
import time
import webbrowser
from pathlib import Path

import typer
import uvicorn

import factory_console
from factory_console.app import create_app
from factory_console.config import Settings, require_loopback_host
from factory_console.file_adapter.discovery import ProjectNotFound, discover_project
from factory_console.file_adapter.manifest import MalformedManifest
from factory_console.file_adapter.real import RealFileAdapter
from factory_console.file_adapter.real_writer import RealFileWriter
from factory_console.file_adapter.watcher_real import RealFileWatcher
from factory_console.logging import LOG_LEVELS, configure_logging, normalize_log_level

_LOGGER = logging.getLogger(__name__)

# How long the browser-opening daemon thread waits for Uvicorn to report
# ``server.started`` before giving up, and how often it polls in the meantime.
# Small so a ready server is opened promptly, bounded so a server that never comes
# up can never leave the thread spinning forever.
_BROWSER_READY_TIMEOUT_S = 5.0
_BROWSER_POLL_INTERVAL_S = 0.05

app = typer.Typer(add_completion=False)


def _format_host_for_url(host: str) -> str:
    """Return ``host`` shaped for an ``http://`` URL, bracketing IPv6 literals.

    ``::1`` becomes ``[::1]`` (so the colon-bearing address is unambiguous against
    the ``:port`` suffix); an IPv4 address or ``localhost`` is returned unchanged.
    """
    return f"[{host}]" if ":" in host else host


def _resolve_port(host: str, port: int) -> int:
    """Bind a throwaway probe socket to choose/confirm ``port``, then release it.

    The address family is picked from ``host`` (``AF_INET6`` for ``::1``, else
    ``AF_INET``). With ``port == 0`` the OS assigns a free ephemeral port, read back
    from ``getsockname()`` so the printed URL and Uvicorn are handed the SAME
    concrete port. With an explicit ``port`` the probe binds it WITHOUT
    ``SO_REUSEADDR``, so an already-bound port raises :class:`OSError` (EADDRINUSE),
    which the caller maps to exit 2. The probe is always closed before returning, so
    Uvicorn is free to bind the port itself.
    """
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.bind((host, port))
        return probe.getsockname()[1]


def _open_browser_when_ready(server: uvicorn.Server, url: str) -> None:
    """Open ``url`` once ``server`` reports it has started, from a daemon thread.

    Polls ``server.started`` up to :data:`_BROWSER_READY_TIMEOUT_S` so the browser
    is only launched against a live server, and gives up silently if the server
    never comes up. The :func:`webbrowser.open` call is wrapped so a headless
    environment (no display, no browser) can never crash the serving process — the
    failure is logged at debug and swallowed, since browser-opening is a
    convenience, not a requirement of serving.
    """
    deadline = time.monotonic() + _BROWSER_READY_TIMEOUT_S
    while not server.started:
        if time.monotonic() >= deadline:
            _LOGGER.debug("browser open skipped: server not ready before timeout")
            return
        time.sleep(_BROWSER_POLL_INTERVAL_S)
    try:
        webbrowser.open(url)
    except Exception:
        _LOGGER.debug("browser open failed (headless environment?)", exc_info=True)


@app.command()
def main(
    path: Path | None = typer.Argument(None),
    port: int = typer.Option(0, "--port", envvar="FACTORY_CONSOLE_PORT"),
    host: str = typer.Option("127.0.0.1", "--host", envvar="FACTORY_CONSOLE_HOST"),
    no_browser: bool = typer.Option(False, "--no-browser"),
    log_level: str = typer.Option("INFO", "--log-level", envvar="FACTORY_CONSOLE_LOG_LEVEL"),
    version: bool = typer.Option(False, "--version"),
) -> None:
    """Discover an App Factory project, then serve the Factory Console over it.

    Validation runs cheapest-first so bad input fails before any filesystem or
    network work: ``--version`` prints ``factory-console v{version}`` and exits 0; a
    non-loopback ``host`` (127.0.0.1 trust boundary) or an unrecognized
    ``--log-level`` exits 2. Logging is then configured and the project root is
    discovered from ``path`` (an explicit path wins, else an upward walk from the
    cwd) — a missing project exits 1. The concrete
    :class:`~factory_console.file_adapter.real.RealFileAdapter` and a
    :class:`~factory_console.file_adapter.watcher_real.RealFileWatcher` rooted at
    that project are wired into :func:`~factory_console.app.create_app` (the app
    lifespan starts/stops the watcher), and the manifest is force-parsed once so a
    malformed ``tickets.json`` exits 3 before a port is bound. The port is then
    resolved via a probe socket (an in-use explicit ``--port`` exits 2), the exact
    contract line is printed to stdout, and Uvicorn serves the app.

    Shutdown is delegated to Uvicorn: it captures SIGINT/SIGTERM, sets
    ``should_exit = True``, and drains (the lifespan shutdown ``stop()``s the
    watcher). A post-shutdown ``KeyboardInterrupt`` (the Python 3.11+ asyncio runner
    re-raises it on SIGINT) is caught so Ctrl-C exits 0.
    Unless ``--no-browser`` is given, a daemon thread opens the served URL once the
    server is ready; a headless environment can never crash the process.
    """
    if version:
        typer.echo(f"factory-console v{factory_console.__version__}")
        raise typer.Exit(0)

    try:
        require_loopback_host(host)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    # Range-check the port up front (cheap-input-first) so an out-of-range value
    # exits 2 with a message like every other bad input, rather than reaching
    # ``socket.bind`` — which raises OverflowError (NOT an OSError, so the bind
    # try/except below would miss it) and dies with a raw traceback.
    if not 0 <= port <= 65535:
        typer.echo(f"port must be between 0 and 65535, got {port}", err=True)
        raise typer.Exit(2)

    normalized_log_level = normalize_log_level(log_level)
    if normalized_log_level is None:
        typer.echo(f"log level must be one of {list(LOG_LEVELS)}, got {log_level!r}", err=True)
        raise typer.Exit(2)
    configure_logging(normalized_log_level)

    try:
        root = discover_project(path, Path.cwd())
    except ProjectNotFound as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    # Canonicalize the root: discover_project returns an explicit PATH verbatim (a
    # relative or symlinked argument stays as typed), but create_app stashes this on
    # app.state and the health/project endpoints report it as the *resolved*
    # projectRoot. Resolve here so that contract holds (the cwd-walk branch already
    # returns a resolved path, so this is a no-op there).
    root = root.resolve()

    file_adapter = RealFileAdapter()
    # ``Settings`` is consulted for the write token alone: host/port/log-level are
    # already resolved above through Typer's own envvar= plumbing. An unset
    # FACTORY_CONSOLE_WRITE_TOKEN leaves this None, which is create_app's cue to mint
    # a fresh per-session token and print it to stderr.
    fastapi_app = create_app(
        file_adapter,
        version=factory_console.__version__,
        project_root=root,
        file_watcher=RealFileWatcher(root),
        file_writer=RealFileWriter(),
        write_token=Settings().write_token,
    )

    # Discovery only checks the manifest FILE exists; force a real parse now so a
    # malformed manifest fails fast (exit 3) at boot rather than on the first request.
    try:
        project = file_adapter.load_project(root)
        file_adapter.list_tickets(project)
    except MalformedManifest as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(3) from exc

    try:
        resolved_port = _resolve_port(host, port)
    except OSError as exc:
        typer.echo(f"port {port} on {host} is already in use", err=True)
        raise typer.Exit(2) from exc

    url = f"http://{_format_host_for_url(host)}:{resolved_port}"
    typer.echo(f"Factory Console v{factory_console.__version__} — serving {root} at {url}")

    config = uvicorn.Config(
        fastapi_app, host=host, port=resolved_port, log_level=normalized_log_level.lower()
    )
    server = uvicorn.Server(config)
    if not no_browser:
        threading.Thread(target=_open_browser_when_ready, args=(server, url), daemon=True).start()
    # Uvicorn traps SIGINT itself and shuts down gracefully (draining then
    # returning), but on Python 3.11+ the asyncio runner re-raises KeyboardInterrupt
    # out of ``server.run()`` *after* that clean shutdown completes. Suppress it so
    # Ctrl-C exits 0 (the CLI contract) instead of 130 — the graceful shutdown has
    # already run. SIGTERM never takes this path: it flips ``should_exit`` and lets
    # ``server.run()`` return normally.
    with contextlib.suppress(KeyboardInterrupt):
        server.run()
