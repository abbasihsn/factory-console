"""Integration tests for the v3.0 registry read endpoints (T112).

Both routes are driven over HTTP against a real ``create_app`` app, because what they
promise is a published CONTRACT the SPA generates types from: the field names, the
``{items, total}`` envelope, the ``condition`` vocabulary and the 200-with-a-``reason``
answer are only actually asserted through a request.

The registry is T107's :class:`FakeProjectRegistry`, so nothing here needs a database.
The PATHS, however, are real ``tmp_path`` directories built by :func:`_project_dir`,
for the same reason ``test_api_selection.py`` gives: ``condition`` is the one thing
these endpoints establish from the filesystem, so a fake path would make every
``path_missing``/``unreadable``/``no_factory_dir`` case a test of the test rather than
of the classifier. The real
:class:`~factory_console.file_adapter.project_condition.RealProjectConditionProbe` is
what the handler instantiates, and it is what runs here.

The condition cases are the milestone's substance, not decoration. A hosted console
shows run-state only for working copies that are on this machine, and the three ways a
row can be un-servable — the path is gone, the console may not read it, it is a real
project the factory has never run against — send an operator to three different fixes.
That is why the union has five members instead of being a boolean, and why the distinct
values are pinned here per row rather than in aggregate.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from factory_console.api.v1.projects import RegisteredProjectOut
from factory_console.app import create_app
from factory_console.domain import Project
from factory_console.domain.registry import RegisteredProject
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.discovery import MANIFEST_RELPATH
from factory_console.services.project_selection import SESSION_PROJECT_ID, SelectionState
from factory_console.store.fake_registry import FakeProjectRegistry

_LIST_ROUTE = "/api/v1/projects"
_CURRENT_ROUTE = "/api/v1/projects/current"

# ``chmod 000`` is meaningless for root (which bypasses the permission bits) and on
# Windows (which has no such mode), so an ``unreadable`` case would silently assert the
# opposite of what it means on those hosts.
_CANNOT_REVOKE_READ = sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0)


def _project_dir(root: Path, *, manifest: bool = True, factory: bool = True) -> Path:
    """Create ``root`` as a project directory in the shape the probe classifies.

    The two flags are the two things
    :func:`~factory_console.file_adapter.project_condition.classify_project_path` looks
    for: the tickets manifest (its absence is ``not_a_project``) and ``.factory/`` (its
    absence is ``no_factory_dir``, the ordinary state of a fresh clone). The manifest
    path is imported from discovery rather than spelled here, so this helper cannot come
    to disagree with the code under test about what makes a directory a project.
    """
    root.mkdir(parents=True, exist_ok=True)
    if manifest:
        (root / MANIFEST_RELPATH).parent.mkdir(parents=True, exist_ok=True)
        (root / MANIFEST_RELPATH).write_text('{"tickets": []}', encoding="utf-8")
    if factory:
        (root / ".factory").mkdir(exist_ok=True)
    return root


def _make_app(project_root: Path, registry: FakeProjectRegistry | None = None) -> FastAPI:
    """Build a real app; no route under test ever reads through the file adapter."""
    project = Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        discoveredAt=datetime(2026, 1, 1),
    )
    return create_app(
        FakeFileAdapter(project=project, tickets=[]),
        version="0.0.0",
        project_root=project_root,
        project_registry=registry,
    )


def _list(app: FastAPI) -> tuple[int, dict]:
    """GET the listing and return ``(status, body)``."""
    response = TestClient(app).get(_LIST_ROUTE)
    return response.status_code, response.json()


def _current(app: FastAPI) -> tuple[int, dict]:
    """GET the current-selection envelope and return ``(status, body)``."""
    response = TestClient(app).get(_CURRENT_ROUTE)
    return response.status_code, response.json()


def _by_id(body: dict) -> dict[str, dict]:
    """Index a listing body's rows by id, so a test names the row it asserts on."""
    return {row["id"]: row for row in body["items"]}


# --------------------------------------------------------------------------- #
# GET /projects — the listing
# --------------------------------------------------------------------------- #


def test_pinned_only_app_lists_the_unregistered_session_row(tmp_path: Path) -> None:
    # No registry at all: pinned mode, which is every pre-v3 app. It is a valid
    # configuration and not an error, so the listing is not empty and not a 4xx — the
    # dropdown is populated from the very first boot, which is what lets the SPA offer
    # "Add this project" as an explicit act.
    root = _project_dir(tmp_path / "pinned")

    status, body = _list(_make_app(root))

    assert status == 200
    assert body == {
        "items": [
            {
                "id": SESSION_PROJECT_ID,
                "name": "pinned",
                "path": str(root),
                "addedAt": None,
                "registered": False,
                "selected": True,
                "condition": "ok",
            }
        ],
        "total": 1,
    }


def test_an_empty_registry_still_lists_the_session_row(tmp_path: Path) -> None:
    # A registry with no rows is a fresh install, not a fault: the boot-time project is
    # still there to be shown, and ``total`` counts it.
    root = _project_dir(tmp_path / "pinned")

    status, body = _list(_make_app(root, FakeProjectRegistry()))

    assert status == 200
    assert [row["id"] for row in body["items"]] == [SESSION_PROJECT_ID]
    assert body["total"] == 1


def test_the_session_row_is_first_and_registered_rows_follow_in_registry_order(
    tmp_path: Path,
) -> None:
    registry = FakeProjectRegistry()
    first = registry.add_project(_project_dir(tmp_path / "alpha"))
    second = registry.add_project(_project_dir(tmp_path / "beta"))

    status, body = _list(_make_app(_project_dir(tmp_path / "pinned"), registry))

    assert status == 200
    assert [row["id"] for row in body["items"]] == [SESSION_PROJECT_ID, first.id, second.id]
    assert body["total"] == 3
    assert all(row["registered"] for row in body["items"][1:])


def test_no_pin_and_no_selection_yields_an_empty_list_rather_than_an_error(
    tmp_path: Path,
) -> None:
    # ``create_app``'s ``project_root`` is required, so a pinless app cannot be built
    # through it — this is the shape v3.1's pathless ``serve`` will boot into, reached
    # by replacing the state the factory installed. An empty registry with no pin has
    # genuinely nothing to list, and saying so with 200 + [] is what lets the SPA render
    # its "add a project" prompt instead of an error card.
    app = _make_app(_project_dir(tmp_path / "pinned"), FakeProjectRegistry())
    app.state.selection = SelectionState(pinned_root=None, registry=app.state.project_registry)

    status, body = _list(app)

    assert status == 200
    assert body == {"items": [], "total": 0}


def test_each_row_reports_its_own_condition_and_none_is_dropped(tmp_path: Path) -> None:
    # The whole reason ``condition`` is a five-member union and not a boolean. Every row
    # survives — MONOTONICITY: a degraded row silently filtered out reads to the user as
    # "I never registered that", a false statement about their own past action — and each
    # one carries the distinct answer that names ITS state.
    registry = FakeProjectRegistry()
    healthy = registry.add_project(_project_dir(tmp_path / "healthy"))
    fresh_clone = registry.add_project(_project_dir(tmp_path / "fresh", factory=False))
    not_a_project = registry.add_project(_project_dir(tmp_path / "plain", manifest=False))
    # Registered while it existed (as any real row was), then deleted underneath the
    # console — the row outliving its directory is the case the union's ``path_missing``
    # member exists for.
    (tmp_path / "gone").mkdir()
    gone = registry.add_project(tmp_path / "gone")
    gone.path.rmdir()

    status, body = _list(_make_app(_project_dir(tmp_path / "pinned"), registry))

    assert status == 200
    rows = _by_id(body)
    assert len(rows) == 5, "no row may be dropped, however degraded"
    assert rows[healthy.id]["condition"] == "ok"
    assert rows[fresh_clone.id]["condition"] == "no_factory_dir"
    assert rows[not_a_project.id]["condition"] == "not_a_project"
    assert rows[gone.id]["condition"] == "path_missing"
    # The removed directory is still a true record of what the user asked for, so its
    # own row keeps naming it.
    assert rows[gone.id]["path"] == str(gone.path)


@pytest.mark.skipif(_CANNOT_REVOKE_READ, reason="chmod 000 does not revoke read for this user/OS")
def test_an_unreadable_row_is_named_unreadable_and_not_missing(tmp_path: Path) -> None:
    # The distinction the probe exists for: a chmod-000 directory STATS perfectly well
    # from a traversable parent, so "I could not look" must never be reported as "there
    # is nothing there" — the first sends an operator to their file modes, the second
    # sends them hunting for a project that never moved.
    registry = FakeProjectRegistry()
    locked = registry.add_project(_project_dir(tmp_path / "locked"))
    app = _make_app(_project_dir(tmp_path / "pinned"), registry)
    locked.path.chmod(0o000)
    try:
        status, body = _list(app)
    finally:
        locked.path.chmod(0o700)

    assert status == 200
    assert _by_id(body)[locked.id]["condition"] == "unreadable"


def test_exactly_the_selected_row_is_flagged_selected(tmp_path: Path) -> None:
    registry = FakeProjectRegistry()
    chosen = registry.add_project(_project_dir(tmp_path / "chosen"))
    other = registry.add_project(_project_dir(tmp_path / "other"))
    app = _make_app(_project_dir(tmp_path / "pinned"), registry)

    # Before switching, the pin IS the session's selection, so the session row is the
    # selected one and no registered row claims to be.
    rows = _by_id(_list(app)[1])
    assert rows[SESSION_PROJECT_ID]["selected"] is True
    assert rows[chosen.id]["selected"] is False

    app.state.selection.select(chosen.id)

    rows = _by_id(_list(app)[1])
    assert rows[chosen.id]["selected"] is True
    assert rows[SESSION_PROJECT_ID]["selected"] is False
    assert rows[other.id]["selected"] is False


def test_the_listing_does_not_fail_when_nothing_is_selected(tmp_path: Path) -> None:
    # ``/projects`` is the screen a user browses in order to LEAVE the no-selection
    # state, so it must not answer with the 409 the read endpoints answer: denying the
    # listing would deny them the fix.
    registry = FakeProjectRegistry()
    row = registry.add_project(_project_dir(tmp_path / "project"))
    app = _make_app(_project_dir(tmp_path / "pinned"), registry)
    app.state.selection = SelectionState(pinned_root=None, registry=registry)

    status, body = _list(app)

    assert status == 200
    assert _by_id(body)[row.id]["selected"] is False


# --------------------------------------------------------------------------- #
# GET /projects/current — the selection envelope
# --------------------------------------------------------------------------- #


def test_current_reports_the_pinned_session_row_on_a_fresh_boot(tmp_path: Path) -> None:
    root = _project_dir(tmp_path / "pinned", factory=False)

    status, body = _current(_make_app(root))

    assert status == 200
    assert body["reason"] is None
    assert body["selected"]["id"] == SESSION_PROJECT_ID
    assert body["selected"]["registered"] is False
    assert body["selected"]["path"] == str(root)
    # Probed, not assumed: booting proves the root was discoverable at boot, and a fresh
    # clone's ``.factory/`` is absent, which the dropdown must be able to say.
    assert body["selected"]["condition"] == "no_factory_dir"


def test_current_reports_the_selected_registered_project(tmp_path: Path) -> None:
    registry = FakeProjectRegistry()
    row = registry.add_project(_project_dir(tmp_path / "chosen"))
    app = _make_app(_project_dir(tmp_path / "pinned"), registry)
    app.state.selection.select(row.id)

    status, body = _current(app)

    assert status == 200
    assert body["reason"] is None
    assert body["selected"] == {
        "id": row.id,
        "name": row.name,
        "path": str(row.path),
        "addedAt": body["selected"]["addedAt"],
        "registered": True,
        "selected": True,
        "condition": "ok",
    }
    assert body["selected"]["addedAt"] is not None, "a registered row always has an addedAt"


def test_current_names_no_selection_instead_of_404ing(tmp_path: Path) -> None:
    # Having nothing selected is the ordinary state of a fresh console, which the SPA
    # renders as a prompt. A 404 would send the user hunting for a URL that was never
    # wrong.
    app = _make_app(_project_dir(tmp_path / "pinned"), FakeProjectRegistry())
    app.state.selection = SelectionState(pinned_root=None, registry=app.state.project_registry)

    status, body = _current(app)

    assert status == 200
    assert body == {"selected": None, "reason": "no_selection"}


def test_current_names_a_selection_whose_row_has_gone(tmp_path: Path) -> None:
    # The row is removed while it is being viewed, so the in-process session selection
    # outlives it. The pinned root is NOT substituted: answering with a different
    # project under the selected project's name is the silent mis-answer the whole
    # selection seam refuses.
    registry = FakeProjectRegistry()
    row = registry.add_project(_project_dir(tmp_path / "removed"))
    app = _make_app(_project_dir(tmp_path / "pinned"), registry)
    app.state.selection.select(row.id)
    registry.remove_project(row.id)

    status, body = _current(app)

    assert status == 200
    assert body == {"selected": None, "reason": "selected_project_not_registered"}


def test_current_names_a_deleted_selected_path_missing(tmp_path: Path) -> None:
    # The reason must be the SAME verdict every other endpoint refuses this selection
    # with, or the SPA would render a healthy header over panels that are all 409ing.
    registry = FakeProjectRegistry()
    (tmp_path / "gone").mkdir()
    row = registry.add_project(tmp_path / "gone")
    app = _make_app(_project_dir(tmp_path / "pinned"), registry)
    app.state.selection.select(row.id)
    row.path.rmdir()

    status, body = _current(app)

    assert status == 200
    assert body == {"selected": None, "reason": "selected_project_missing"}


@pytest.mark.skipif(_CANNOT_REVOKE_READ, reason="chmod 000 does not revoke read for this user/OS")
def test_current_names_an_unreadable_selected_path_unreadable(tmp_path: Path) -> None:
    registry = FakeProjectRegistry()
    row = registry.add_project(_project_dir(tmp_path / "locked"))
    app = _make_app(_project_dir(tmp_path / "pinned"), registry)
    app.state.selection.select(row.id)
    row.path.chmod(0o000)
    try:
        status, body = _current(app)
    finally:
        row.path.chmod(0o700)

    assert status == 200
    assert body == {"selected": None, "reason": "selected_project_unreadable"}


def test_a_readable_project_the_factory_never_ran_is_still_the_current_selection(
    tmp_path: Path,
) -> None:
    # ``no_factory_dir`` is degraded but USABLE — plan, tickets and roadmap all read
    # normally — and it is not a member of ``SelectionFailure``, so it must arrive as a
    # SELECTED row carrying its condition rather than as a refusal.
    registry = FakeProjectRegistry()
    row = registry.add_project(_project_dir(tmp_path / "fresh", factory=False))
    app = _make_app(_project_dir(tmp_path / "pinned"), registry)
    app.state.selection.select(row.id)

    status, body = _current(app)

    assert status == 200
    assert body["reason"] is None
    assert body["selected"]["condition"] == "no_factory_dir"


# --------------------------------------------------------------------------- #
# Shared behaviour of both routes
# --------------------------------------------------------------------------- #


def test_a_store_io_failure_is_a_503_not_a_500_on_both_routes(tmp_path: Path) -> None:
    # An OSError or sqlite3.Error out of the store is the console failing to read its
    # OWN database. It is deliberately NOT folded into ``reason``: that union names
    # states of the user's SELECTION, and "I could not look at all" is not one of them,
    # so it stays the 503 the domain-error handler renders.
    class _GoesUnreadableRegistry(FakeProjectRegistry):
        """A registry that starts healthy and then cannot be read.

        The order matters: the selection is made against a working store, exactly as a
        real one would be, and the store fails only afterwards — the mid-session loss of
        the console's state directory, not a store that was never usable. The two methods
        raise the two error families the real store produces, since a handler catching
        only ``OSError`` would let ``SqliteProjectRegistry``'s own failures through as a
        bare, undiagnosed 500.
        """

        readable = True

        def list_projects(self) -> list[RegisteredProject]:
            if not self.readable:
                raise OSError("state directory is not readable")
            return super().list_projects()

        def get_project(self, project_id: str) -> RegisteredProject | None:
            if not self.readable:
                raise sqlite3.OperationalError("database is locked")
            return super().get_project(project_id)

    registry = _GoesUnreadableRegistry()
    row = registry.add_project(_project_dir(tmp_path / "project"))
    app = _make_app(_project_dir(tmp_path / "pinned"), registry)
    app.state.selection.select(row.id)
    registry.readable = False

    list_status, list_body = _list(app)
    current_status, current_body = _current(app)

    assert list_status == 503
    assert list_body["error"]["code"] == "registry_unreadable"
    assert current_status == 503
    assert current_body["error"]["code"] == "registry_unreadable"


def test_a_row_round_trips_through_the_forbidding_wire_model(tmp_path: Path) -> None:
    # The published shape is exactly what the model declares: nothing the SPA's
    # generated types would not know about, and nothing missing. ``extra="forbid"``
    # is what makes a store column that grew a wire field fail here loudly instead
    # of leaking, so it is asserted in both directions.
    registry = FakeProjectRegistry()
    registry.add_project(_project_dir(tmp_path / "project"))
    status, body = _list(_make_app(_project_dir(tmp_path / "pinned"), registry))

    assert status == 200
    for row in body["items"]:
        assert RegisteredProjectOut.model_validate(row).model_dump(mode="json") == row
        with pytest.raises(ValidationError, match="extra_forbidden"):
            RegisteredProjectOut.model_validate({**row, "lastOpenedAt": "2026-08-07T00:00:00Z"})
