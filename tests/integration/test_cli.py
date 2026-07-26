"""Integration tests for the real ``factory-console`` Typer entrypoint (T25).

Two complementary layers:

* **Subprocess** — the ticket's explicit end-to-end requirement. Each case launches
  ``[sys.executable, "-m", "factory_console", ...]`` with a ``PYTHONPATH`` pointing
  at *this* worktree's ``server/`` (never the possibly-stale global console
  script), so the child runs the code under test on macOS and Linux alike. The
  happy path parses the printed contract URL, hits ``/api/v1/health`` over it, and
  asserts a clean SIGINT exit 0; the failure cases assert exit codes 1/2/3.
* **In-process** — fast :class:`~typer.testing.CliRunner` runs that also give
  coverage of ``cli.py`` (subprocess execution is invisible to coverage.py). The
  full boot path is driven with ``uvicorn.Server`` stubbed to a no-op so
  ``server.run()`` returns immediately without ever binding a real socket.
"""

import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import factory_console
from factory_console import cli
from factory_console.cli import app
from factory_console.config import WRITE_TOKEN_HEADER

runner = CliRunner()

# Every ``FACTORY_CONSOLE_*`` variable the CLI reads through Typer's ``envvar=``.
_ENV_VARS = (
    "FACTORY_CONSOLE_HOST",
    "FACTORY_CONSOLE_PORT",
    "FACTORY_CONSOLE_LOG_LEVEL",
    "FACTORY_CONSOLE_WRITE_TOKEN",
)


@pytest.fixture(autouse=True)
def _clear_ambient_console_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip ambient ``FACTORY_CONSOLE_*`` values so every case starts from the defaults.

    These vars are real CLI inputs (``envvar=``), so one exported in the developer's
    shell silently rewrites the command under test: ``FACTORY_CONSOLE_HOST=0.0.0.0``
    turns any invocation that omits ``--host`` into an exit-2 run, and a bogus
    ``FACTORY_CONSOLE_LOG_LEVEL`` does the same. That reaches the subprocess cases too,
    since ``_child_env`` inherits ``os.environ``.

    Cases that WANT a variable set still pass it via ``runner.invoke(env=...)``, which
    applies on top of this — so precedence tests are unaffected.
    """
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


_TESTS_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVER_DIR = _REPO_ROOT / "server"
_FIXTURES = _TESTS_DIR / "fixtures" / "projects"
_MINIMAL = _FIXTURES / "minimal"
_MALFORMED = _FIXTURES / "malformed"

# Matches the URL inside the contract line for both IPv4 (127.0.0.1) and bracketed
# IPv6 (`[::1]`) hosts, capturing the resolved port.
_CONTRACT_URL_RE = re.compile(r"http://(?:\[[0-9a-fA-F:]+\]|[\d.]+):\d+")


# --------------------------------------------------------------------------- #
# In-process stubs
# --------------------------------------------------------------------------- #


class _StubServer:
    """Stand-in for ``uvicorn.Server`` whose ``run()`` is a no-op.

    Reports ``started = True`` so any browser-opening path sees a "ready" server;
    ``run()`` returns immediately so the in-process boot path never binds a real
    socket or blocks the test.
    """

    started = True

    def __init__(self, config: object) -> None:
        self.config = config

    def run(self) -> None:
        return None


class _SyncThread:
    """Stand-in for ``threading.Thread`` that runs its target synchronously.

    Lets the "open the browser" branch be exercised deterministically in-process:
    ``start()`` invokes the target inline instead of racing a real daemon thread.
    """

    def __init__(self, target: object, args: tuple = (), daemon: bool = False) -> None:
        self._target = target
        self._args = args

    def start(self) -> None:
        self._target(*self._args)


class _ReadyServer:
    """Minimal server double for :func:`_open_browser_when_ready` unit tests."""

    started = True


# --------------------------------------------------------------------------- #
# Subprocess helpers
# --------------------------------------------------------------------------- #


def _child_env() -> dict[str, str]:
    """Return the child env with this worktree's ``server/`` prepended to PYTHONPATH."""
    existing = os.environ.get("PYTHONPATH", "")
    entries = [str(_SERVER_DIR), *([existing] if existing else [])]
    return {**os.environ, "PYTHONPATH": os.pathsep.join(entries)}


def _launch(*args: str, **popen_kwargs: object) -> subprocess.Popen:
    """Launch ``python -m factory_console <args>`` against this worktree's code."""
    return subprocess.Popen(
        [sys.executable, "-m", "factory_console", *args],
        env=_child_env(),
        **popen_kwargs,
    )


def _read_contract_url(proc: subprocess.Popen, timeout: float) -> str | None:
    """Read ``proc``'s stdout until the contract URL line appears, or return ``None``.

    Bounded by a wall-clock ``timeout`` and by the child dying (``poll()`` /
    EOF) so a launch that never prints the line can never hang the test.
    """
    deadline = time.monotonic() + timeout
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return None
        line = proc.stdout.readline()
        if line == "":
            return None
        if "Factory Console" in line:
            match = _CONTRACT_URL_RE.search(line)
            if match is not None:
                return match.group(0)
    return None


def _terminate(proc: subprocess.Popen) -> None:
    """Best-effort teardown so no child process leaks past a test."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    if proc.stdout is not None:
        proc.stdout.close()


def _get_health(url: str, timeout: float) -> httpx.Response:
    """GET ``{url}/api/v1/health``, retrying until the server is accepting connections.

    The contract line is printed *before* ``server.run()`` binds the socket, so the
    first request can race the server's startup and be refused. Retry on
    :class:`httpx.ConnectError` up to ``timeout`` rather than sleeping a fixed
    (flaky) amount.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            return httpx.get(f"{url}/api/v1/health", timeout=5.0)
        except httpx.ConnectError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


# --------------------------------------------------------------------------- #
# Subprocess tests (end-to-end)
# --------------------------------------------------------------------------- #


def test_subprocess_boot_serves_health_and_exits_zero_on_sigint() -> None:
    # Since T44 the CLI also constructs a RealFileWatcher and hands it to
    # create_app, so this boot exercises the watcher lifecycle end-to-end: the app
    # lifespan start()s it (the ``minimal`` fixture has docs/planning, so the
    # observer schedules a real recursive watch) and stop()s it on the SIGINT
    # drain. A clean exit 0 with no hang proves the observer thread is joined.
    proc = _launch(
        str(_MINIMAL),
        "--no-browser",
        "--port",
        "0",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        url = _read_contract_url(proc, timeout=10.0)
        assert url is not None, "CLI never printed the contract URL line"

        resp = _get_health(url, timeout=10.0)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["projectRoot"] == str(_MINIMAL)

        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=3) == 0
    finally:
        _terminate(proc)


def test_subprocess_unknown_path_exits_one(tmp_path: Path) -> None:
    missing = tmp_path / "not-a-project"
    proc = _launch(
        str(missing),
        "--no-browser",
        "--port",
        "0",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert proc.wait(timeout=10) == 1
    finally:
        _terminate(proc)


def test_subprocess_malformed_manifest_exits_three() -> None:
    proc = _launch(
        str(_MALFORMED),
        "--no-browser",
        "--port",
        "0",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert proc.wait(timeout=10) == 3
    finally:
        _terminate(proc)


def test_subprocess_port_in_use_exits_two() -> None:
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        proc = _launch(
            str(_MINIMAL),
            "--no-browser",
            "--port",
            str(port),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            assert proc.wait(timeout=10) == 2
        finally:
            _terminate(proc)
    finally:
        holder.close()


def test_subprocess_version_exits_zero() -> None:
    proc = _launch(
        "--version",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        out, _ = proc.communicate(timeout=10)
        assert proc.returncode == 0
        assert f"factory-console v{factory_console.__version__}" in out
    finally:
        _terminate(proc)


# --------------------------------------------------------------------------- #
# In-process tests (CliRunner) — fast + coverage of cli.py
# --------------------------------------------------------------------------- #


def test_version_flag_prints_prefixed_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"factory-console v{factory_console.__version__}"


def test_non_loopback_host_is_rejected() -> None:
    result = runner.invoke(app, ["--host", "0.0.0.0"])
    assert result.exit_code == 2


def test_unknown_log_level_is_rejected() -> None:
    result = runner.invoke(app, ["--log-level", "bogus"])
    assert result.exit_code == 2


def test_out_of_range_port_is_rejected() -> None:
    # A port past 65535 must fail fast with exit 2 (a clean message) rather than
    # reaching socket.bind, which raises OverflowError (not caught by the bind's
    # except OSError) and dies with a raw traceback.
    result = runner.invoke(app, ["--port", "70000"])
    assert result.exit_code == 2


def test_env_var_configures_log_level_when_no_flag_given(monkeypatch: pytest.MonkeyPatch) -> None:
    # The documented FACTORY_CONSOLE_* env vars must actually take effect (Typer
    # envvar=): with no --log-level flag, FACTORY_CONSOLE_LOG_LEVEL drives logging.
    captured: dict[str, object] = {}
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", _StubServer)
    monkeypatch.setattr(
        "factory_console.cli.configure_logging",
        lambda level: captured.__setitem__("level", level),
    )

    result = runner.invoke(
        app,
        [str(_MINIMAL), "--no-browser", "--port", "0"],
        env={"FACTORY_CONSOLE_LOG_LEVEL": "debug"},
    )

    assert result.exit_code == 0
    assert captured["level"] == "DEBUG"


def test_explicit_log_level_flag_overrides_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    # Precedence: an explicit flag wins over the env var (which wins over the default).
    captured: dict[str, object] = {}
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", _StubServer)
    monkeypatch.setattr(
        "factory_console.cli.configure_logging",
        lambda level: captured.__setitem__("level", level),
    )

    result = runner.invoke(
        app,
        [str(_MINIMAL), "--no-browser", "--port", "0", "--log-level", "warning"],
        env={"FACTORY_CONSOLE_LOG_LEVEL": "debug"},
    )

    assert result.exit_code == 0
    assert captured["level"] == "WARNING"


def test_explicit_host_flag_overrides_a_non_loopback_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same precedence for --host, and the boot must not re-read the env behind the
    # flag's back: the CLI reads the write token via ``read_write_token()``, which
    # touches FACTORY_CONSOLE_WRITE_TOKEN alone, so a non-loopback
    # FACTORY_CONSOLE_HOST that --host overrode cannot resurface as an unhandled
    # pydantic ValidationError and bypass the exit-2 contract.
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", _StubServer)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)

    result = runner.invoke(
        app,
        [str(_MINIMAL), "--no-browser", "--port", "0", "--host", "127.0.0.1"],
        env={"FACTORY_CONSOLE_HOST": "0.0.0.0"},
    )

    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:" in result.output


def test_env_var_pins_the_write_token_on_the_built_app(monkeypatch: pytest.MonkeyPatch) -> None:
    # The pass-through itself: FACTORY_CONSOLE_WRITE_TOKEN must actually reach
    # app.state.write_token. Without this, dropping the create_app kwarg in a refactor
    # would leave every test green while the operator's pin silently stopped working.
    pinned = "cli-pinned-write-token"
    captured: dict[str, object] = {}

    class _CapturingServer(_StubServer):
        def __init__(self, config: object) -> None:
            super().__init__(config)
            captured["app"] = config.app  # type: ignore[attr-defined]

    monkeypatch.setattr("factory_console.cli.uvicorn.Server", _CapturingServer)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)

    result = runner.invoke(
        app,
        [str(_MINIMAL), "--no-browser", "--port", "0"],
        env={"FACTORY_CONSOLE_WRITE_TOKEN": pinned},
    )

    assert result.exit_code == 0, result.output
    assert captured["app"].state.write_token == pinned  # type: ignore[union-attr]


@pytest.mark.parametrize("pin", ["", "too-short"], ids=["blank", "short"])
def test_unusable_write_token_pin_exits_two(pin: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # A pin that is set but unusable is a bad operational input, so it exits 2 with a
    # message like a bad host or log level — never a raw pydantic traceback, and never
    # a silent fall back to a generated token that would 401 every write.
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", _StubServer)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)

    result = runner.invoke(
        app,
        [str(_MINIMAL), "--no-browser", "--port", "0"],
        env={"FACTORY_CONSOLE_WRITE_TOKEN": pin},
    )

    assert result.exit_code == 2, result.output
    assert "FACTORY_CONSOLE_WRITE_TOKEN" in result.output


def test_full_boot_prints_contract_line_and_configures_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", _StubServer)
    monkeypatch.setattr(
        "factory_console.cli.configure_logging",
        lambda level: captured.__setitem__("level", level),
    )

    result = runner.invoke(
        app, [str(_MINIMAL), "--no-browser", "--port", "0", "--log-level", "debug"]
    )

    assert result.exit_code == 0
    assert captured["level"] == "DEBUG"
    prefix = (
        f"Factory Console v{factory_console.__version__} — serving {_MINIMAL} at http://127.0.0.1:"
    )
    assert prefix in result.output
    match = re.search(r"http://127\.0\.0\.1:(\d+)", result.output)
    assert match is not None and int(match.group(1)) > 0


def test_unknown_path_in_process_exits_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", _StubServer)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)
    missing = tmp_path / "not-a-project"

    result = runner.invoke(app, [str(missing), "--no-browser", "--port", "0"])

    assert result.exit_code == 1


def test_malformed_manifest_in_process_exits_three(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", _StubServer)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)

    result = runner.invoke(app, [str(_MALFORMED), "--no-browser", "--port", "0"])

    assert result.exit_code == 3


def test_explicit_port_in_use_in_process_exits_two(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", _StubServer)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        holder.bind(("127.0.0.1", 0))
        port = holder.getsockname()[1]
        result = runner.invoke(app, [str(_MINIMAL), "--no-browser", "--port", str(port)])
        assert result.exit_code == 2
    finally:
        holder.close()


def test_malformed_manifest_announces_no_write_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``create_app`` mints AND announces the per-session token, so building it before
    # the boot guards printed a real secret for a server that then exited 3 without
    # binding a socket. Pinning the absence here is what stops the construction from
    # drifting back above the guards: the exit code alone stays 3 either way.
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", _StubServer)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)

    result = runner.invoke(app, [str(_MALFORMED), "--no-browser", "--port", "0"])

    assert result.exit_code == 3
    assert WRITE_TOKEN_HEADER not in result.output


def test_port_in_use_announces_no_write_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # The operator-facing trap this ordering closes: a second console started on a
    # taken port used to print a fresh token before exiting 2. Copying it authorizes
    # nothing — the RUNNING instance holds a different one — and the deliberately
    # opaque 401 gives no way to tell why.
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", _StubServer)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        holder.bind(("127.0.0.1", 0))
        port = holder.getsockname()[1]

        result = runner.invoke(app, [str(_MINIMAL), "--no-browser", "--port", str(port)])

        assert result.exit_code == 2
        assert WRITE_TOKEN_HEADER not in result.output
    finally:
        holder.close()


def test_successful_boot_still_announces_the_write_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # The other half of the ordering fix: moving construction below the guards must not
    # cost the announcement on the path that DOES serve, or the operator has no token.
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", _StubServer)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)

    result = runner.invoke(app, [str(_MINIMAL), "--no-browser", "--port", "0"])

    assert result.exit_code == 0
    assert f"{WRITE_TOKEN_HEADER}: " in result.output


def test_boot_starts_browser_thread_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: dict[str, str] = {}
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", _StubServer)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)
    monkeypatch.setattr("factory_console.cli.threading.Thread", _SyncThread)
    monkeypatch.setattr(
        "factory_console.cli._open_browser_when_ready",
        lambda server, url: opened.__setitem__("url", url),
    )

    result = runner.invoke(app, [str(_MINIMAL), "--port", "0"])

    assert result.exit_code == 0
    assert opened["url"].startswith("http://127.0.0.1:")


# --------------------------------------------------------------------------- #
# Unit tests for the small CLI helpers
# --------------------------------------------------------------------------- #


def test_format_host_for_url_brackets_ipv6() -> None:
    assert cli._format_host_for_url("::1") == "[::1]"
    assert cli._format_host_for_url("127.0.0.1") == "127.0.0.1"


def test_open_browser_when_ready_opens_url(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: dict[str, str] = {}
    monkeypatch.setattr(
        "factory_console.cli.webbrowser.open", lambda u: opened.__setitem__("url", u)
    )
    cli._open_browser_when_ready(_ReadyServer(), "http://127.0.0.1:9001")
    assert opened["url"] == "http://127.0.0.1:9001"


def test_open_browser_when_ready_swallows_headless_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_url: str) -> None:
        raise RuntimeError("no display")

    monkeypatch.setattr("factory_console.cli.webbrowser.open", _boom)
    # Must not raise: a headless environment can never crash the process.
    cli._open_browser_when_ready(_ReadyServer(), "http://127.0.0.1:9001")


def test_open_browser_when_ready_gives_up_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("factory_console.cli._BROWSER_READY_TIMEOUT_S", 0.0)
    opened = {"called": False}
    monkeypatch.setattr(
        "factory_console.cli.webbrowser.open",
        lambda u: opened.__setitem__("called", True),
    )

    class _NeverReady:
        started = False

    cli._open_browser_when_ready(_NeverReady(), "http://127.0.0.1:9001")
    assert opened["called"] is False


def test_open_browser_when_ready_polls_until_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("factory_console.cli.time.sleep", lambda _s: None)
    opened: dict[str, str] = {}
    monkeypatch.setattr(
        "factory_console.cli.webbrowser.open", lambda u: opened.__setitem__("url", u)
    )

    class _EventuallyReady:
        """Reports not-started on the first poll, started on the second."""

        def __init__(self) -> None:
            self._checks = 0

        @property
        def started(self) -> bool:
            self._checks += 1
            return self._checks >= 2

    cli._open_browser_when_ready(_EventuallyReady(), "http://127.0.0.1:9001")
    assert opened["url"] == "http://127.0.0.1:9001"
