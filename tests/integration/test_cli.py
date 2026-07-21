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
    # T20 gave create_app a required file_adapter arg; cli.py still calls it zero-arg
    # (full wiring lands in T25), so stub it here to keep the boot path testable.
    monkeypatch.setattr("factory_console.cli.create_app", lambda: object())

    result = runner.invoke(app, ["--port", "8765", "--log-level", "DEBUG"])

    assert result.exit_code == 0
    assert calls["log_level"] == "DEBUG"
    assert calls["run_kwargs"] == {"host": "127.0.0.1", "port": 8765, "log_level": "debug"}


def test_lowercase_log_level_is_normalized_and_boots(monkeypatch: pytest.MonkeyPatch) -> None:
    # A lowercase level (the natural uvicorn form) must be normalized to the
    # uppercase name ``logging`` requires, not crash before boot.
    calls: dict[str, object] = {}

    def fake_run(_app: object, **kwargs: object) -> None:
        calls["run_kwargs"] = kwargs

    def fake_configure(level: str) -> None:
        calls["log_level"] = level

    monkeypatch.setattr("factory_console.cli.uvicorn.run", fake_run)
    monkeypatch.setattr("factory_console.cli.configure_logging", fake_configure)
    # T20 gave create_app a required file_adapter arg; cli.py still calls it zero-arg
    # (full wiring lands in T25), so stub it here to keep the boot path testable.
    monkeypatch.setattr("factory_console.cli.create_app", lambda: object())

    result = runner.invoke(app, ["--log-level", "debug"])

    assert result.exit_code == 0
    assert calls["log_level"] == "DEBUG"
    assert calls["run_kwargs"]["log_level"] == "debug"


def test_unknown_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    booted = False

    def fake_run(_app: object, **kwargs: object) -> None:
        nonlocal booted
        booted = True

    monkeypatch.setattr("factory_console.cli.uvicorn.run", fake_run)

    result = runner.invoke(app, ["--log-level", "bogus"])

    assert result.exit_code == 2
    assert not booted


def test_non_loopback_host_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    booted = False

    def fake_run(_app: object, **kwargs: object) -> None:
        nonlocal booted
        booted = True

    monkeypatch.setattr("factory_console.cli.uvicorn.run", fake_run)

    result = runner.invoke(app, ["--host", "0.0.0.0"])

    assert result.exit_code != 0
    assert not booted
