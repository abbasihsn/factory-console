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
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import factory_console
from factory_console import cli
from factory_console.cli import app
from factory_console.config import WRITE_TOKEN_HEADER
from factory_console.file_adapter.run_artifacts import RealRunArtifactReader
from factory_console.file_adapter.watcher_real import RealFileWatcher
from factory_console.services.project_selection import SESSION_PROJECT_ID
from factory_console.store.sqlite_registry import SqliteProjectRegistry

runner = CliRunner()

# Every ``FACTORY_CONSOLE_*`` variable the CLI reads, through Typer's ``envvar=`` or
# (``FACTORY_CONSOLE_DB_PATH``) through the store's own settings object.
_ENV_VARS = (
    "FACTORY_CONSOLE_HOST",
    "FACTORY_CONSOLE_PORT",
    "FACTORY_CONSOLE_LOG_LEVEL",
    "FACTORY_CONSOLE_WRITE_TOKEN",
    "FACTORY_CONSOLE_DB_PATH",
)


@pytest.fixture(autouse=True)
def _clear_ambient_console_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip ambient ``FACTORY_CONSOLE_*`` values so every case starts from the defaults.

    These vars are real CLI inputs (``envvar=``), so one exported in the developer's
    shell silently rewrites the command under test: ``FACTORY_CONSOLE_HOST=0.0.0.0``
    turns any invocation that omits ``--host`` into an exit-2 run, and a bogus
    ``FACTORY_CONSOLE_LOG_LEVEL`` does the same. That reaches the subprocess cases too,
    since ``_child_env`` inherits ``os.environ``.

    ``FACTORY_CONSOLE_DB_PATH`` is stripped for the same reason one step removed: it is
    not a Typer option, but the CLI now opens a
    :class:`~factory_console.store.sqlite_registry.SqliteProjectRegistry` through it, so
    an ambient value would decide which store the cases below address. Clearing it points
    the default construction at the operator's real path — which is harmless precisely
    because construction is side-effect-free (T108): nothing here CALLS the registry
    without first pointing ``FACTORY_CONSOLE_DB_PATH`` at a ``tmp_path`` file, so no test
    can create ``~/.factory-console/``.

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
# The second App Factory project, so a listing/switch case has a project to name that
# is unmistakably not the booted one.
_SECOND = _FIXTURES / "second"

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


def _capturing_server() -> tuple[type[_StubServer], dict[str, object]]:
    """Return a fresh capturing ``uvicorn.Server`` stub plus the dict it fills.

    Lets a test assert on what ``create_app`` actually BUILT — the only way to
    prove a ``create_app`` kwarg survives the boot, since the CLI never hands the
    app back. A new class and a new dict per call, deliberately: a shared or
    class-level capture would leak one test's app into the next.
    """
    captured: dict[str, object] = {}

    class _CapturingServer(_StubServer):
        def __init__(self, config: object) -> None:
            super().__init__(config)
            captured["app"] = config.app  # type: ignore[attr-defined]

    return _CapturingServer, captured


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
    server, captured = _capturing_server()

    monkeypatch.setattr("factory_console.cli.uvicorn.Server", server)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)

    result = runner.invoke(
        app,
        [str(_MINIMAL), "--no-browser", "--port", "0"],
        env={"FACTORY_CONSOLE_WRITE_TOKEN": pinned},
    )

    assert result.exit_code == 0, result.output
    assert captured["app"].state.write_token == pinned  # type: ignore[union-attr]


def test_production_boot_wires_the_real_run_artifact_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The production composition root must bind the artifact-read port, or
    # GET /api/v1/runs raises the seam's wiring RuntimeError on a real boot. The
    # kwarg executes on every CLI test (so it is line-covered) but nothing asserted
    # it REACHED app.state — a dropped or misnamed kwarg would leave the suite green
    # and only surface when an operator hit the endpoint.
    server, captured = _capturing_server()

    monkeypatch.setattr("factory_console.cli.uvicorn.Server", server)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)

    result = runner.invoke(app, [str(_MINIMAL), "--no-browser", "--port", "0"])

    assert result.exit_code == 0, result.output
    reader = captured["app"].state.run_artifact_reader  # type: ignore[union-attr]
    assert isinstance(reader, RealRunArtifactReader)


# --------------------------------------------------------------------------- #
# v3.0 wiring (T119): the registry, the watcher factory, the ephemeral session pin
# --------------------------------------------------------------------------- #


def test_production_boot_wires_the_registry_and_the_watcher_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # v3.0's turn-on, asserted at the composition root: the CLI must hand create_app
    # BOTH a real registry (or the switcher has no rows to offer) and RealFileWatcher
    # itself as the ``watcher_factory`` (or the supervisor can hold the boot watcher
    # but never build a successor, so the first project switch silently leaves the
    # console watcher-less with live updates dead and no error anywhere).
    server, captured = _capturing_server()
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", server)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)

    result = runner.invoke(
        app,
        [str(_MINIMAL), "--no-browser", "--port", "0"],
        env={"FACTORY_CONSOLE_DB_PATH": str(tmp_path / "console.db")},
    )

    assert result.exit_code == 0, result.output
    state = captured["app"].state  # type: ignore[union-attr]
    assert isinstance(state.project_registry, SqliteProjectRegistry)
    assert state.watcher_supervisor._factory is RealFileWatcher
    # The pin is the boot root, and it is NOT registered: the ephemeral-session rule.
    assert state.selection.pinned_root == _MINIMAL


def test_boot_does_not_register_the_discovered_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The discovered root is an EPHEMERAL, UNREGISTERED session project. Auto-registering
    # it would make a read-only viewing invocation — a throwaway clone, a CI job, a
    # Playwright boot — write a permanent row into the operator's console db, so every
    # such boot would grow the dropdown for good. Asserted against the db FILE rather
    # than the listing, so a future listing change cannot hide a write that happened.
    db_path = tmp_path / "console.db"
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", _StubServer)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)

    result = runner.invoke(
        app,
        [str(_MINIMAL), "--no-browser", "--port", "0"],
        env={"FACTORY_CONSOLE_DB_PATH": str(db_path)},
    )

    assert result.exit_code == 0, result.output
    # A boot that touched the store at all would have created the file (the registry's
    # first method call is what creates it), so its absence proves nothing was written.
    assert not db_path.exists()


def test_unaddressable_db_path_warns_and_still_boots_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A store the console cannot even ADDRESS must not take the local viewer down: the
    # single-project viewer never needed a database and must not start needing one. A
    # blank FACTORY_CONSOLE_DB_PATH is the deterministic, cross-platform way in (the
    # store rejects it rather than resolving it to the cwd), and the answer must be a
    # stderr warning plus ``project_registry=None`` — pinned mode, i.e. exactly the
    # pre-v3 behaviour — never an exit code.
    #
    # An UNWRITABLE directory deliberately does NOT take this branch: construction is
    # side-effect-free (T108), so that failure surfaces at the registry's first method
    # call, where the endpoints answer it as the named ``registry_unreadable`` 503.
    server, captured = _capturing_server()
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", server)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)

    result = runner.invoke(
        app,
        [str(_MINIMAL), "--no-browser", "--port", "0"],
        env={"FACTORY_CONSOLE_DB_PATH": ""},
    )

    assert result.exit_code == 0, result.output
    # Click's CliRunner captures the two streams separately; the warning is an operator
    # notice, so it belongs on stderr and stdout must still carry only the contract line.
    assert "could not open the project registry" in result.stderr
    assert "could not open the project registry" not in result.stdout
    contract = f"Factory Console v{factory_console.__version__} — serving {_MINIMAL} at "
    assert contract in result.stdout
    state = captured["app"].state  # type: ignore[union-attr]
    assert state.project_registry is None
    assert state.selection.pinned_root == _MINIMAL


def test_fresh_boot_lists_exactly_the_session_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The dropdown on a first-ever boot: one row, the reserved ``session`` sentinel for
    # the pinned root, ``registered: false`` (so the SPA offers "Add this project"
    # rather than "Remove") and selected. An empty registry must contribute nothing —
    # and, since the CLI does not auto-register, there is nothing for it to contribute.
    server, captured = _capturing_server()
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", server)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)

    result = runner.invoke(
        app,
        [str(_MINIMAL), "--no-browser", "--port", "0"],
        env={"FACTORY_CONSOLE_DB_PATH": str(tmp_path / "console.db")},
    )
    assert result.exit_code == 0, result.output

    # No lifespan (a bare TestClient, not the context-manager form): the listing needs
    # no watcher, and starting one would spin a real watchdog observer for an assertion
    # about rows.
    body = TestClient(captured["app"]).get("/api/v1/projects").json()  # type: ignore[arg-type]

    assert body["total"] == 1
    (row,) = body["items"]
    assert row["id"] == SESSION_PROJECT_ID
    assert row["path"] == str(_MINIMAL)
    assert row["registered"] is False
    assert row["selected"] is True
    assert row["addedAt"] is None


def test_boot_lists_a_pre_registered_second_project_beside_the_session_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The other half of the wiring: rows that were registered BEFORE this boot must show
    # up, which is what proves the CLI opened the store the operator's
    # FACTORY_CONSOLE_DB_PATH names rather than an empty one of its own. ``second/`` is
    # the fixture a switch has somewhere to switch TO — a different project name and a
    # disjoint ticket-id space from ``minimal/``.
    db_path = tmp_path / "console.db"
    registered = SqliteProjectRegistry(db_path).add_project(_SECOND)

    server, captured = _capturing_server()
    monkeypatch.setattr("factory_console.cli.uvicorn.Server", server)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)

    result = runner.invoke(
        app,
        [str(_MINIMAL), "--no-browser", "--port", "0"],
        env={"FACTORY_CONSOLE_DB_PATH": str(db_path)},
    )
    assert result.exit_code == 0, result.output

    body = TestClient(captured["app"]).get("/api/v1/projects").json()  # type: ignore[arg-type]

    # Session row first (the boot project is what the dropdown opens on), then the
    # registered one — present but not selected, since a pin never yields the selection.
    assert [row["id"] for row in body["items"]] == [SESSION_PROJECT_ID, registered.id]
    assert [row["selected"] for row in body["items"]] == [True, False]
    assert body["items"][1]["path"] == str(_SECOND)
    assert body["items"][1]["registered"] is True


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
