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
from factory_console.config import LOOPBACK_HOSTS
from factory_console.logging import configure_logging

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
    is rejected (exit 2) to hold the 127.0.0.1 trust boundary. Otherwise logging is
    configured and Uvicorn serves ``create_app()`` on ``host``/``port``. Real path
    discovery, port handling, browser opening, and exit codes arrive in backend
    T25; ``path`` and ``no_browser`` are accepted-but-unused stubs for now.
    """
    if version:
        typer.echo(factory_console.__version__)
        raise typer.Exit(0)

    if host not in LOOPBACK_HOSTS:
        typer.echo(
            f"host must be a loopback address {sorted(LOOPBACK_HOSTS)}, got {host!r}",
            err=True,
        )
        raise typer.Exit(2)

    configure_logging(log_level)
    uvicorn.run(create_app(), host=host, port=port, log_level=log_level.lower())
