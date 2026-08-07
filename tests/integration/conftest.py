"""Shared fixtures for the integration suite.

The one fixture here is package-wide isolation: every integration test's
``FACTORY_CONSOLE_DB_PATH`` is pointed at its own ``tmp_path`` before the test body
runs, so no test — nor any subprocess it launches with the ambient environment,
per ``test_cli.py`` — can create or touch the developer's (or CI runner's) real
``~/.factory-console/console.db``. See T104's :func:`~factory_console.store.location.resolve_db_path`
for why this one variable is the whole override surface.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_console_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``FACTORY_CONSOLE_DB_PATH`` at this test's own ``tmp_path`` by default.

    ``monkeypatch.setenv`` mutates real ``os.environ``, so a subprocess launched with
    the ambient environment (``tests/integration/test_cli.py``'s ``_launch``) inherits
    this path too, not just in-process construction. A case that wants a SPECIFIC path
    still overrides it explicitly (an env kwarg to ``runner.invoke``, or a subprocess
    env override) — those apply on top of this default, same precedence as any other
    ``FACTORY_CONSOLE_*`` variable.
    """
    monkeypatch.setenv("FACTORY_CONSOLE_DB_PATH", str(tmp_path / "console.db"))
