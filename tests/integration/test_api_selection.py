"""Integration tests for the v3.0 selection seam (T111).

Every branch of ``Depends(get_current_project_root)`` is driven through a THROWAWAY
route mounted on a real ``create_app`` app, rather than by calling the dependency
directly. That is deliberate: the seam's whole job is to be consumed by FastAPI, and
only a real request proves the offloads run on a worker thread, the errors reach
``register_error_handlers``, and the 409/503 envelopes come out with the codes the
contract names. No production endpoint consumes the seam yet, so a probe route is the
only honest way to exercise it.

The registry is T107's :class:`FakeProjectRegistry`, so nothing here needs a database.
The PATHS, however, are real ``tmp_path`` directories, because the one thing the probe
must get right is what the filesystem says — a fake path would make every "missing"
and "unreadable" case a test of the test.

The last three cases are the milestone's precedence acceptance criteria, and they are
the reason this seam was written down rather than left implicit: a pinned boot beats a
persisted selection, a later ``select()`` beats the pin, and the CLI's ``serving``
line keeps naming the path the operator typed.
"""

import os
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Mount
from typer.testing import CliRunner

import factory_console
from factory_console.api.deps import (
    get_current_project_root,
    get_project_registry,
    get_selection_state,
)
from factory_console.app import create_app
from factory_console.cli import app as cli_app
from factory_console.domain import Project
from factory_console.domain.registry import RegisteredProject
from factory_console.file_adapter import FakeFileAdapter
from factory_console.services.project_selection import SESSION_PROJECT_ID, SelectionState
from factory_console.store.fake_registry import FakeProjectRegistry

_PROBE_ROUTE = "/probe-selected-root"

_FIXTURE_PROJECT = Path(__file__).resolve().parents[1] / "fixtures" / "projects" / "minimal"

# Every ``FACTORY_CONSOLE_*`` variable the CLI reads through Typer's ``envvar=``; one
# left in the developer's shell would silently rewrite the invocation under test.
_CLI_ENV_VARS = (
    "FACTORY_CONSOLE_HOST",
    "FACTORY_CONSOLE_PORT",
    "FACTORY_CONSOLE_LOG_LEVEL",
    "FACTORY_CONSOLE_WRITE_TOKEN",
)

# ``chmod 000`` is meaningless for root (which bypasses the permission bits) and on
# Windows (which has no such mode), so the unreadable case would silently assert the
# opposite of what it means on those hosts.
_CANNOT_REVOKE_READ = sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0)


def _make_fake_adapter() -> FakeFileAdapter:
    """Build a minimal :class:`FakeFileAdapter`; no route here ever reads through it."""
    project = Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        discoveredAt=datetime(2026, 1, 1),
    )
    return FakeFileAdapter(project=project, tickets=[])


def _make_app(project_root: Path, registry: FakeProjectRegistry | None = None) -> FastAPI:
    """Build a real app plus the throwaway route that resolves the selected root."""
    app = create_app(
        _make_fake_adapter(),
        version="0.0.0",
        project_root=project_root,
        project_registry=registry,
    )

    @app.get(_PROBE_ROUTE)
    async def _probe(root: Path = Depends(get_current_project_root)) -> dict[str, str]:
        return {"root": str(root)}

    # ``create_app`` mounts the SPA catch-all at "/" LAST, so a probe route added after
    # it returns would be shadowed whenever a packaged ``_static/`` is on disk. Move the
    # mount back to last (a no-op in a dev checkout, where ``_static/`` is absent), as
    # ``tests/integration/test_app_factory.py`` does for the same reason.
    for mount in [r for r in app.router.routes if isinstance(r, Mount) and r.name == "static"]:
        app.router.routes.remove(mount)
        app.router.routes.append(mount)

    return app


def _resolve(app: FastAPI) -> tuple[int, dict]:
    """GET the probe route and return ``(status, body)``."""
    response = TestClient(app).get(_PROBE_ROUTE)
    return response.status_code, response.json()


# --------------------------------------------------------------------------- #
# Resolution branches
# --------------------------------------------------------------------------- #


def test_pinned_only_app_serves_the_pinned_root(tmp_path: Path) -> None:
    # No registry at all: the app is permanently pinned, which is every pre-v3 app.
    # The pin is served WITHOUT a registry read or a stat, so it works for a root that
    # would fail a probe — the boot-time discovery already established it.
    status, body = _resolve(_make_app(tmp_path))

    assert status == 200
    assert body == {"root": str(tmp_path)}


def test_selected_registered_project_is_served(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(other)
    app = _make_app(tmp_path / "pinned", registry)
    app.state.selection.select(row.id)

    status, body = _resolve(app)

    assert status == 200
    assert body == {"root": str(row.path)}


def test_selection_of_an_unregistered_id_refuses_instead_of_falling_back(tmp_path: Path) -> None:
    # The row is removed while it is being viewed, so the in-process session selection
    # outlives it. MONOTONICITY: the seam must NOT quietly fall back to the pinned root
    # (which exists and would render), because that answers a question about the
    # selected project with a different project's tickets, under the selected name.
    other = tmp_path / "other"
    other.mkdir()
    pinned = tmp_path / "pinned"
    pinned.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(other)
    app = _make_app(pinned, registry)
    app.state.selection.select(row.id)
    registry.remove_project(row.id)

    status, body = _resolve(app)

    assert status == 409
    assert body["error"]["code"] == "selected_project_not_registered"
    assert body["error"]["details"]["reason"] == "selected_project_not_registered"
    assert body["error"]["details"]["projectId"] == row.id
    assert str(pinned) not in body["error"]["message"]


def test_deleted_selected_path_is_named_missing(tmp_path: Path) -> None:
    gone = tmp_path / "gone"
    gone.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(gone)
    app = _make_app(tmp_path / "pinned", registry)
    app.state.selection.select(row.id)
    gone.rmdir()

    status, body = _resolve(app)

    assert status == 409
    assert body["error"]["code"] == "selected_project_unavailable"
    assert body["error"]["details"]["reason"] == "selected_project_missing"
    assert str(row.path) in body["error"]["message"]


def test_selected_path_replaced_by_a_file_is_named_missing(tmp_path: Path) -> None:
    # A path that exists but is not a directory names no project directory, so it is
    # reported as missing rather than as a readable root the endpoints would then fail
    # to load a manifest out of.
    swapped = tmp_path / "swapped"
    swapped.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(swapped)
    app = _make_app(tmp_path / "pinned", registry)
    app.state.selection.select(row.id)
    swapped.rmdir()
    swapped.write_text("not a project")

    status, body = _resolve(app)

    assert status == 409
    assert body["error"]["details"]["reason"] == "selected_project_missing"


@pytest.mark.skipif(_CANNOT_REVOKE_READ, reason="chmod 000 does not revoke read for this user/OS")
def test_unreadable_selected_path_is_named_unreadable_not_missing(tmp_path: Path) -> None:
    # The distinction the probe exists for: a chmod-000 directory STATS perfectly well
    # from a traversable parent, so a stat-only probe would call it healthy, and an
    # errno-blind one would call it missing and send the operator hunting for a
    # directory that never moved. It is unreadable, and says so.
    locked = tmp_path / "locked"
    locked.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(locked)
    app = _make_app(tmp_path / "pinned", registry)
    app.state.selection.select(row.id)
    locked.chmod(0o000)
    try:
        status, body = _resolve(app)
    finally:
        locked.chmod(0o700)

    assert status == 409
    assert body["error"]["code"] == "selected_project_unavailable"
    assert body["error"]["details"]["reason"] == "selected_project_unreadable"


def test_no_registry_and_no_pin_is_no_project_selected(tmp_path: Path) -> None:
    # ``create_app``'s ``project_root`` is required, so a pinless selection cannot be
    # built through it — this is the shape v3.1's pathless ``serve`` will boot into,
    # exercised by replacing the state the factory installed.
    app = _make_app(tmp_path)
    app.state.selection = SelectionState(pinned_root=None, registry=None)

    status, body = _resolve(app)

    assert status == 409
    assert body["error"]["code"] == "no_project_selected"
    assert body["error"]["details"]["reason"] == "no_selection"


def test_persisted_selection_is_read_through_when_nothing_is_pinned(tmp_path: Path) -> None:
    # The read-through half of the rule: with no session pin, the DURABLE selection is
    # what a boot resumes at, which is what makes ``PUT /projects/current`` outlive the
    # process that set it.
    project = tmp_path / "resumed"
    project.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(project)
    registry.set_selected_project(row.id)
    app = _make_app(tmp_path, registry)
    app.state.selection = SelectionState(pinned_root=None, registry=registry)

    status, body = _resolve(app)

    assert status == 200
    assert body == {"root": str(row.path)}


def test_registry_io_failure_is_a_503_not_a_500(tmp_path: Path) -> None:
    # An OSError out of the store is the console failing to read its OWN database, not
    # a statement about the user's selection, so it is a named 503 rather than a bare
    # 500 the operator has nothing to act on.
    project = tmp_path / "project"
    project.mkdir()

    class _GoesUnreadableRegistry(FakeProjectRegistry):
        """A registry that starts healthy and then cannot be read.

        The order matters: the selection is made against a working store, exactly as a
        real one would be, and the store fails only afterwards — the mid-session loss
        of the console's state directory, not a store that was never usable.
        """

        readable = True

        def get_project(self, project_id: str) -> RegisteredProject | None:
            if not self.readable:
                raise OSError("state directory is not readable")
            return super().get_project(project_id)

    registry = _GoesUnreadableRegistry()
    row = registry.add_project(project)
    app = _make_app(tmp_path / "pinned", registry)
    app.state.selection.select(row.id)
    registry.readable = False

    status, body = _resolve(app)

    assert status == 503
    assert body["error"]["code"] == "registry_unreadable"


# --------------------------------------------------------------------------- #
# Precedence: PATH vs the persisted selection
# --------------------------------------------------------------------------- #


def test_boot_with_a_path_serves_it_over_a_persisted_selection(tmp_path: Path) -> None:
    # Acceptance (a). The registry already points at another project — as it would for
    # an operator who switched yesterday — and ``factory-console <pinned>`` must still
    # serve the path they just typed, or the CLI contract is a lie.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    pinned = tmp_path / "pinned"
    pinned.mkdir()
    registry = FakeProjectRegistry()
    persisted = registry.add_project(elsewhere)
    registry.set_selected_project(persisted.id)

    app = _make_app(pinned, registry)
    status, body = _resolve(app)

    assert status == 200
    assert body == {"root": str(pinned)}
    assert app.state.selection.current_id() == SESSION_PROJECT_ID
    # The pin is a SESSION fact and never reaches the user's database: a read-only
    # look at another directory must not overwrite the selection they made in the UI.
    assert registry.get_selected_project() is not None
    assert registry.get_selected_project().id == persisted.id


def test_select_takes_effect_in_the_same_process_and_persists(tmp_path: Path) -> None:
    # Acceptance (b). Without this, ``PUT /projects/current`` would change nothing any
    # endpoint reads until the next boot, and the milestone's headline feature would be
    # inert in the only invocation v3.0 ships.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    pinned = tmp_path / "pinned"
    pinned.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(elsewhere)
    app = _make_app(pinned, registry)

    assert _resolve(app) == (200, {"root": str(pinned)})

    app.state.selection.select(row.id)

    assert _resolve(app) == (200, {"root": str(elsewhere)})
    selected = registry.get_selected_project()
    assert selected is not None and selected.id == row.id


def test_cli_serving_line_names_the_boot_time_pinned_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # Acceptance (c). The seam must be invisible to the CLI's machine-parsable stdout
    # contract line, which promises the operator the path they typed — and the app it
    # boots must agree, pinned at that same root and starting at the session sentinel.
    for var in _CLI_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    captured: dict[str, object] = {}

    class _CapturingServer:
        started = True

        def __init__(self, config: object) -> None:
            captured["app"] = config.app  # type: ignore[attr-defined]

        def run(self) -> None:
            return None

    monkeypatch.setattr("factory_console.cli.uvicorn.Server", _CapturingServer)
    monkeypatch.setattr("factory_console.cli.configure_logging", lambda level: None)

    result = CliRunner().invoke(cli_app, [str(_FIXTURE_PROJECT), "--no-browser", "--port", "0"])

    assert result.exit_code == 0, result.output
    prefix = f"Factory Console v{factory_console.__version__} — serving {_FIXTURE_PROJECT} at "
    assert prefix in result.output

    booted = captured["app"]
    assert booted.state.project_root == _FIXTURE_PROJECT  # type: ignore[attr-defined]
    assert booted.state.selection.pinned_root == _FIXTURE_PROJECT  # type: ignore[attr-defined]
    assert booted.state.selection.current_id() == SESSION_PROJECT_ID  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# SelectionState itself
# --------------------------------------------------------------------------- #


def test_selecting_the_session_sentinel_is_never_persisted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(project)
    selection = SelectionState(pinned_root=tmp_path, registry=registry)
    selection.select(row.id)

    selection.select(SESSION_PROJECT_ID)

    assert selection.current_id() == SESSION_PROJECT_ID
    # The sentinel names no row, so writing it would violate the registry's foreign
    # key; the persisted selection is left exactly where the operator put it.
    persisted = registry.get_selected_project()
    assert persisted is not None and persisted.id == row.id


def test_subscribers_receive_the_newly_resolved_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(project)
    selection = SelectionState(pinned_root=tmp_path, registry=registry)
    seen: list[Path | None] = []
    callback: Callable[[Path | None], None] = seen.append
    selection.subscribe(callback)

    selection.select(row.id)
    selection.select(SESSION_PROJECT_ID)
    selection.select(None)

    assert seen == [row.path, tmp_path, None]


# --------------------------------------------------------------------------- #
# The two state-reading providers
# --------------------------------------------------------------------------- #


def test_get_project_registry_returns_none_in_pinned_mode(tmp_path: Path) -> None:
    # A registry-less app is a valid configuration (pinned mode), so this provider
    # degrades like ``get_file_watcher`` instead of raising like ``get_file_adapter``.
    request = SimpleNamespace(app=_make_app(tmp_path))
    assert get_project_registry(request) is None  # type: ignore[arg-type]


def test_get_project_registry_returns_the_registry_bound_by_create_app(tmp_path: Path) -> None:
    registry = FakeProjectRegistry()
    request = SimpleNamespace(app=_make_app(tmp_path, registry))
    assert get_project_registry(request) is registry  # type: ignore[arg-type]


def test_get_selection_state_raises_when_unbound() -> None:
    # Unlike the registry, an absent SelectionState is a wiring bug: ``create_app``
    # always builds one, so this can only mean the app did not come from it.
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(RuntimeError, match="selection"):
        get_selection_state(request)  # type: ignore[arg-type]
