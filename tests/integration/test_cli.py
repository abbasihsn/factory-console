"""Integration tests for the walking-skeleton Typer CLI.

``--version`` is exercised directly; the boot path is exercised with ``uvicorn.run``
and ``configure_logging`` monkeypatched so no real server ever starts in tests.
"""

import pytest
from typer.testing import CliRunner

import factory_console
from factory_console.cli import app

runner = CliRunner()


def test_version_flag_prints_version_and_exits_zero() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == factory_console.__version__


def test_boot_configures_logging_and_runs_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    def fake_run(_app: object, **kwargs: object) -> None:
        calls["run_kwargs"] = kwargs

    def fake_configure(level: str) -> None:
        calls["log_level"] = level

    monkeypatch.setattr("factory_console.cli.uvicorn.run", fake_run)
    monkeypatch.setattr("factory_console.cli.configure_logging", fake_configure)

    result = runner.invoke(app, ["--port", "8765", "--log-level", "DEBUG"])

    assert result.exit_code == 0
    assert calls["log_level"] == "DEBUG"
    assert calls["run_kwargs"] == {"host": "127.0.0.1", "port": 8765, "log_level": "debug"}
