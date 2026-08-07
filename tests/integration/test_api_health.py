"""Integration tests for the selection-aware ``GET /api/v1/health`` probe.

The health handler moved out of ``app.py`` into ``api/v1/health.py`` (T24) and, as of
T116, reports the SELECTED project rather than the root pinned at boot: ``projectRoot``
is nullable and a named ``selectionReason`` drawn from T111's ``SelectionFailure``
union says which condition holds.

The point of every case below is the same assertion — ``ok`` is ``True``. This is the
endpoint an operator and the SPA's boot sequence hit to find out WHY nothing else
answers, so it must report a missing or broken selection as a named condition at
``200`` and never as a 409, a 503, or a fabricated root.

Every state is driven, so all four ``SelectionFailure`` members are asserted: pinned
(which is every pre-v3 app, and must be unchanged apart from the two new fields),
nothing selected, and selected-but-unusable in each of its shapes — the row is gone,
the row's directory is gone, and the row's directory cannot be read. That last one
matters as much as the others: ``missing`` and ``unreadable`` send an operator to
different places, so a probe that collapsed them would be precisely as unhelpful as one
that fabricated a root. Registry work runs against T107's
:class:`FakeProjectRegistry`, but the PATHS are real ``tmp_path`` directories, because
"the selected directory was deleted" is only a real test if a real directory is really
deleted.
"""

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import factory_console
from factory_console.app import create_app
from factory_console.domain import Project
from factory_console.domain.registry import RegisteredProject
from factory_console.file_adapter import FakeFileAdapter
from factory_console.services.project_selection import SESSION_PROJECT_ID, SelectionState
from factory_console.store.fake_registry import FakeProjectRegistry

_PROJECT = Project(
    rootPath=Path("/factory/demo-project"),
    ticketsManifestPath=Path("/factory/demo-project/docs/planning/tickets.json"),
    ticketsDir=Path("/factory/demo-project/docs/planning/tickets"),
    discoveredAt=datetime(2026, 7, 21, 12, 30, 0),
)
_ROOT = Path("/factory/demo-project")

# ``chmod 000`` is meaningless for root (which bypasses the permission bits) and on
# Windows (which has no such mode), so an ``unreadable`` case would silently assert the
# opposite of what it means on those hosts. Mirrors ``test_api_projects.py``.
_CANNOT_REVOKE_READ = sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0)


def _make_app(
    project_root: Path = _ROOT,
    registry: FakeProjectRegistry | None = None,
) -> FastAPI:
    """Build the real app over an empty-ticket FakeFileAdapter bound to ``project_root``."""
    return create_app(
        FakeFileAdapter(project=_PROJECT, tickets=[]),
        version=factory_console.__version__,
        project_root=project_root,
        project_registry=registry,
    )


def _health(app: FastAPI) -> dict:
    """GET the probe and return its body, asserting the status is always ``200``."""
    resp = TestClient(app).get("/api/v1/health")
    assert resp.status_code == 200
    return resp.json()


# --------------------------------------------------------------------------- #
# The three selection states — ``ok`` is ``True`` in every one
# --------------------------------------------------------------------------- #


def test_health_reports_the_pinned_root_with_no_selection_reason() -> None:
    # Pinned mode is every pre-v3 app: the session sentinel names the typed PATH, so
    # the probe reports a resolved root and no failure at all.
    body = _health(_make_app())

    assert body == {
        "ok": True,
        "version": factory_console.__version__,
        "projectRoot": str(_ROOT),
        "selectedProjectId": SESSION_PROJECT_ID,
        "selectionReason": None,
    }


def test_health_reports_no_selection_as_a_named_absence_not_an_outage() -> None:
    # The state a pathless boot starts in. The probe must NOT 409 here — it is what
    # the SPA asks to discover that the console has nothing selected, so a refusal
    # would report a fixable misconfiguration as a broken server.
    app = _make_app()
    app.state.selection = SelectionState(pinned_root=None, registry=None)

    body = _health(app)

    assert body["ok"] is True
    assert body["projectRoot"] is None
    assert body["selectedProjectId"] is None
    assert body["selectionReason"] == "no_selection"


def test_health_reports_a_deleted_selected_path_and_still_names_the_project(
    tmp_path: Path,
) -> None:
    # The selection is valid and the DIRECTORY is gone. The probe still names both the
    # id and the path, because those are what send the operator somewhere: the reason
    # says what broke, the path says where to go and look.
    gone = tmp_path / "gone"
    gone.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(gone)
    app = _make_app(tmp_path / "pinned", registry)
    app.state.selection.select(row.id)
    gone.rmdir()

    body = _health(app)

    assert body["ok"] is True
    assert body["projectRoot"] == str(row.path)
    assert body["selectedProjectId"] == row.id
    assert body["selectionReason"] == "selected_project_missing"


@pytest.mark.skipif(_CANNOT_REVOKE_READ, reason="chmod 000 does not revoke read for this user/OS")
def test_health_reports_an_unreadable_selected_path(tmp_path: Path) -> None:
    # The fourth ``SelectionFailure`` member, and the one the probe must not collapse
    # into "missing": a chmod-000 directory STATS perfectly well, so reporting it as
    # absent would send the operator hunting for a directory that was there all along.
    locked = tmp_path / "locked"
    locked.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(locked)
    app = _make_app(tmp_path / "pinned", registry)
    app.state.selection.select(row.id)
    locked.chmod(0o000)
    try:
        body = _health(app)
    finally:
        locked.chmod(0o700)

    assert body["ok"] is True
    assert body["projectRoot"] == str(row.path)
    assert body["selectedProjectId"] == row.id
    assert body["selectionReason"] == "selected_project_unreadable"


def test_health_reports_a_selection_whose_row_was_removed(tmp_path: Path) -> None:
    # The id outlived its row. There is no path to report — nothing knows where that
    # project was — but the id is still reported, so the UI can say WHICH selection
    # went stale rather than silently resetting to "nothing selected".
    other = tmp_path / "other"
    other.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(other)
    app = _make_app(tmp_path / "pinned", registry)
    app.state.selection.select(row.id)
    registry.remove_project(row.id)

    body = _health(app)

    assert body["ok"] is True
    assert body["projectRoot"] is None
    assert body["selectedProjectId"] == row.id
    assert body["selectionReason"] == "selected_project_not_registered"


def test_health_reports_a_healthy_selected_project(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(selected)
    app = _make_app(tmp_path / "pinned", registry)
    app.state.selection.select(row.id)

    body = _health(app)

    assert body["ok"] is True
    assert body["projectRoot"] == str(row.path)
    assert body["selectedProjectId"] == row.id
    assert body["selectionReason"] is None


def test_health_stays_ok_when_the_registry_itself_cannot_be_read(tmp_path: Path) -> None:
    # An unreadable store is a 503 on ``GET /api/v1/projects``; here it is reported,
    # not raised. ``RegistryUnreadable`` is deliberately not a ``SelectionFailure``
    # member, so it becomes "cannot currently say" — all three fields null, which no
    # other branch produces — rather than a fifth, invented reason.
    project = tmp_path / "project"
    project.mkdir()

    class _GoesUnreadableRegistry(FakeProjectRegistry):
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

    body = _health(app)

    assert body["ok"] is True
    assert body["projectRoot"] is None
    assert body["selectedProjectId"] is None
    assert body["selectionReason"] is None


def test_health_reports_unknown_when_only_the_first_selection_read_fails(
    tmp_path: Path,
) -> None:
    # The probe reads the selection TWICE — once for the id it reports, once inside the
    # resolution seam — and a TRANSIENT store failure can hit one and not the other.
    # Both must reach the same conclusion. When only the first read fails, answering
    # with a resolved ``projectRoot`` beside a null id and a null reason would emit the
    # one combination ``HealthResponse`` documents as impossible, and would spend the
    # all-null shape that is reserved for "cannot currently say" on a probe that could
    # in fact say. So the failure is reported, not swallowed into "nothing selected".
    project = tmp_path / "project"
    project.mkdir()

    class _FailsFirstReadRegistry(FakeProjectRegistry):
        """Raises on the first read-through only, then behaves — a locked db, briefly."""

        reads = 0

        def get_selected_project(self) -> RegisteredProject | None:
            self.reads += 1
            if self.reads == 1:
                raise sqlite3.OperationalError("database is locked")
            return super().get_selected_project()

    registry = _FailsFirstReadRegistry()
    row = registry.add_project(project)
    registry.set_selected_project(row.id)
    # ``pinned_root=None`` is what leaves the session selection unset, so ``current_id``
    # READS THROUGH to the persisted selection and the failure is reachable at all.
    app = _make_app(tmp_path / "pinned", registry)
    app.state.selection = SelectionState(pinned_root=None, registry=registry)

    body = _health(app)

    assert body["ok"] is True
    assert body["projectRoot"] is None
    assert body["selectedProjectId"] is None
    assert body["selectionReason"] is None
    # The store recovered after that one raise, so the resolution the probe skipped
    # WOULD have produced a root. That is what makes the all-null answer a decision
    # rather than a consequence — and it is the assertion that fails if the first
    # read's failure is ever swallowed back into "nothing selected".
    assert registry.get_selected_project() == row


# --------------------------------------------------------------------------- #
# Frozen OpenAPI shape
# --------------------------------------------------------------------------- #


def test_openapi_publishes_prefixed_health_path_with_nullable_project_root() -> None:
    schema = TestClient(_make_app()).get("/api/v1/openapi.json").json()

    assert schema["openapi"].startswith("3")
    assert "/api/v1/health" in schema["paths"]
    # The breaking narrowing the frontend client (T121) and the e2e harness (T120)
    # have to absorb: ``projectRoot`` is no longer a plain string.
    properties = schema["components"]["schemas"]["HealthResponse"]["properties"]
    assert set(properties) == {
        "ok",
        "version",
        "projectRoot",
        "selectedProjectId",
        "selectionReason",
    }
    assert any(option.get("type") == "null" for option in properties["projectRoot"]["anyOf"])
