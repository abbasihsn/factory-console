"""Walking-skeleton Typer CLI entrypoint for Factory Console.

Exposes the full ``factory-console`` flag surface (``PATH``, ``--port``,
``--host``, ``--no-browser``, ``--log-level``, ``--version``) but implements only
``--version`` and a plain ``uvicorn.run`` boot. Real project-path discovery, port
selection, browser opening, signal handling, and process exit codes live in
backend T25 — ``path`` and ``no_browser`` are accepted-but-unused stubs until then.
"""

from pathlib import Path

import typer
import uvicorn

import factory_console
from factory_console.app import create_app
from factory_console.config import require_loopback_host
from factory_console.logging import LOG_LEVELS, configure_logging, normalize_log_level

app = typer.Typer(add_completion=False)


@app.command()
def main(
    path: Path | None = typer.Argument(None),
    port: int = typer.Option(0, "--port"),
    host: str = typer.Option("127.0.0.1", "--host"),
    no_browser: bool = typer.Option(False, "--no-browser"),
    log_level: str = typer.Option("INFO", "--log-level"),
    version: bool = typer.Option(False, "--version"),
) -> None:
    """Boot the walking-skeleton Factory Console server (or print the version).

    ``--version`` prints the package version and exits 0. A non-loopback ``host``
    (127.0.0.1 trust boundary) or an unrecognized ``--log-level`` is rejected with
    exit 2. Otherwise logging is configured and Uvicorn serves ``create_app()`` on
    ``host``/``port``. Real path discovery, port handling, browser opening, and exit
    codes arrive in backend T25; ``path`` and ``no_browser`` are accepted-but-unused
    stubs for now.
    """
    if version:
        typer.echo(factory_console.__version__)
        raise typer.Exit(0)

    try:
        require_loopback_host(host)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    normalized_log_level = normalize_log_level(log_level)
    if normalized_log_level is None:
        typer.echo(f"log level must be one of {list(LOG_LEVELS)}, got {log_level!r}", err=True)
        raise typer.Exit(2)

    configure_logging(normalized_log_level)
    uvicorn.run(create_app(), host=host, port=port, log_level=normalized_log_level.lower())
