"""``python -m factory_console`` entrypoint that runs THIS package's Typer app.

Delegates straight to the :data:`~factory_console.cli.app` Typer application so the
module form is behaviourally identical to the installed ``factory-console`` console
script. The integration tests launch the CLI this way — ``[sys.executable, "-m",
"factory_console", ...]`` under a ``PYTHONPATH`` pointing at this worktree's
``server/`` — precisely so they exercise the code under test, not whatever the
globally-installed (editable, possibly-stale) console script happens to point at.
"""

from factory_console.cli import app

if __name__ == "__main__":
    app()
