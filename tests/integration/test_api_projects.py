"""Integration tests for the v3.0 registry endpoints — the reads (T112), the writes (T113).

All five routes are driven over HTTP against a real ``create_app`` app, because what they
promise is a published CONTRACT the SPA generates types from: the field names, the
``{items, total}`` envelope, the ``condition`` vocabulary and the 200-with-a-``reason``
answer are only actually asserted through a request.

The three mutations are additionally the only routes here behind the write token, so
each one's gate is asserted alongside its effect: the token is what stops another local
process — or a drive-by browser request, since the console runs no CORS policy and no
CSRF token — from making the console open an arbitrary path on this machine and serve
it over HTTP. Whether a route is gated at all is pinned in one place in
``test_api_write_token.py``; what is pinned HERE is that a rejected request also
CHANGED NOTHING, which the gate table cannot see.

The mutation tests that register a path use the REAL
:class:`~factory_console.file_adapter.real.RealFileAdapter`, not the fake. ``POST
/projects`` validates a candidate directory by discovering a project in it, and
:meth:`~factory_console.file_adapter.fake.FakeFileAdapter.load_project` answers its
seeded project for any path at all — so the fake would make the "this is not an App
Factory project" refusal untestable, and every other add would pass for the wrong
reason.

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
from factory_console.config import WRITE_TOKEN_HEADER
from factory_console.domain import Project
from factory_console.domain.registry import RegisteredProject
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.discovery import MANIFEST_RELPATH
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.file_adapter.real import RealFileAdapter
from factory_console.services.project_selection import SESSION_PROJECT_ID, SelectionState
from factory_console.store.fake_registry import FakeProjectRegistry

_LIST_ROUTE = "/api/v1/projects"
_CURRENT_ROUTE = "/api/v1/projects/current"
# The project-scoped read the selection actually feeds, used to prove a switch moved
# what EVERY endpoint answers rather than just what ``/projects/current`` reports.
_PROJECT_ROUTE = "/api/v1/project"

# The write token every mutation must present, and a same-length near-miss.
PINNED_TOKEN = "pinned-write-token-for-tests"
WRONG_TOKEN = "pinned-write-token-for-tesXX"
AUTH = {WRITE_TOKEN_HEADER: PINNED_TOKEN}

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


def _make_app(
    project_root: Path,
    registry: FakeProjectRegistry | None = None,
    adapter: FileAdapter | None = None,
) -> FastAPI:
    """Build a real app; the read routes never read through the file adapter.

    ``adapter`` defaults to a :class:`FakeFileAdapter` because that is true of every
    read test. The add route is the one that DOES consult it — to decide whether a
    candidate directory is an App Factory project — so those tests pass a
    :class:`RealFileAdapter` and let the real discovery answer.
    """
    project = Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        discoveredAt=datetime(2026, 1, 1),
    )
    return create_app(
        adapter if adapter is not None else FakeFileAdapter(project=project, tickets=[]),
        version="0.0.0",
        project_root=project_root,
        project_registry=registry,
        write_token=PINNED_TOKEN,
    )


def _real_app(tmp_path: Path, registry: FakeProjectRegistry | None = None) -> FastAPI:
    """Build an app over the real adapter, pinned at a real project directory."""
    return _make_app(
        _project_dir(tmp_path / "pinned"),
        FakeProjectRegistry() if registry is None else registry,
        adapter=RealFileAdapter(),
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


def test_a_store_io_failure_is_a_503_not_a_500_on_the_write_routes(tmp_path: Path) -> None:
    # The write-side twin of ``test_a_store_io_failure_is_a_503_not_a_500_on_both_routes``:
    # ``deps.py``'s guard-widening covers the three MUTATIONS too, not just the reads, so
    # a store the console cannot reach must answer the same named 503 on a POST, a
    # DELETE and a PUT rather than a bare 500.
    class _GoesUnreadableRegistry(FakeProjectRegistry):
        """A registry that starts healthy, then fails the write each route depends on.

        ``remove_project`` is deliberately able to succeed once even after ``readable``
        goes false — see ``removed_ok`` — so the DELETE case below can exercise the
        on-loop selection-clearing call that follows a successful removal, rather than
        only the removal itself.
        """

        readable = True
        removed_ok = False

        def add_project(self, path: Path | str, name: str | None = None) -> RegisteredProject:
            if not self.readable:
                raise OSError("state directory is not readable")
            return super().add_project(path, name)

        def remove_project(self, project_id: str) -> bool:
            if not self.readable and not self.removed_ok:
                raise OSError("state directory is not readable")
            return super().remove_project(project_id)

        def set_selected_project(self, project_id: str | None) -> RegisteredProject | None:
            if not self.readable:
                raise sqlite3.OperationalError("database is locked")
            return super().set_selected_project(project_id)

    # POST /projects — fails inside ``_register_project``'s ``registry.add_project``.
    add_registry = _GoesUnreadableRegistry()
    add_app = _make_app(_project_dir(tmp_path / "add-pinned"), add_registry)
    add_registry.readable = False
    add_response = TestClient(add_app).post(
        _LIST_ROUTE, json={"path": str(tmp_path / "add-candidate")}, headers=AUTH
    )
    assert add_response.status_code == 503
    assert add_response.json()["error"]["code"] == "registry_unreadable"

    # PUT /projects/current — fails inside ``_resolve_and_persist``'s
    # ``registry.set_selected_project``.
    select_registry = _GoesUnreadableRegistry()
    chosen = select_registry.add_project(_project_dir(tmp_path / "chosen"))
    select_app = _make_app(_project_dir(tmp_path / "select-pinned"), select_registry)
    select_registry.readable = False
    select_response = TestClient(select_app).put(
        _CURRENT_ROUTE, json={"projectId": chosen.id}, headers=AUTH
    )
    assert select_response.status_code == 503
    assert select_response.json()["error"]["code"] == "registry_unreadable"

    # DELETE /projects/{project_id}, with the row PRE-SELECTED — the removal itself
    # succeeds (``removed_ok``), so it is the on-loop selection-clearing call after it
    # (``was_selected`` -> ``_resolve_and_persist(None)``) that hits the failing store.
    delete_registry = _GoesUnreadableRegistry()
    tracked = delete_registry.add_project(_project_dir(tmp_path / "tracked"))
    delete_app = _make_app(_project_dir(tmp_path / "delete-pinned"), delete_registry)
    client = TestClient(delete_app)
    select = client.put(_CURRENT_ROUTE, json={"projectId": tracked.id}, headers=AUTH)
    assert select.status_code == 200
    delete_registry.readable = False
    delete_registry.removed_ok = True
    delete_response = client.delete(f"{_LIST_ROUTE}/{tracked.id}", headers=AUTH)
    assert delete_response.status_code == 503
    assert delete_response.json()["error"]["code"] == "registry_unreadable"


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


# --------------------------------------------------------------------------- #
# The write-token gate on all three mutations
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "headers",
    [{}, {WRITE_TOKEN_HEADER: WRONG_TOKEN}],
    ids=["no-header", "wrong-header"],
)
def test_every_mutation_rejects_a_bad_token_and_changes_nothing(
    headers: dict[str, str], tmp_path: Path
) -> None:
    # The gate has to fail CLOSED, not merely answer 401: an unauthenticated POST that
    # still registered the path would have already granted the arbitrary-path read the
    # token exists to withhold. So the registry and the selection are both re-read
    # afterwards and must be exactly as they were.
    registry = FakeProjectRegistry()
    existing = registry.add_project(_project_dir(tmp_path / "existing"))
    app = _real_app(tmp_path, registry)
    candidate = _project_dir(tmp_path / "candidate")
    client = TestClient(app)

    rejected = [
        client.post(_LIST_ROUTE, json={"path": str(candidate)}, headers=headers),
        client.delete(f"{_LIST_ROUTE}/{existing.id}", headers=headers),
        client.put(_CURRENT_ROUTE, json={"projectId": existing.id}, headers=headers),
    ]

    for response in rejected:
        assert response.status_code == 401, response.request.method
        error = response.json()["error"]
        assert error["code"] == "write_token_invalid", response.request.method
        # The opaque envelope the SPA's existing WriteTokenPrompt already handles.
        assert set(error) == {"code", "message"}, response.request.method

    assert [row.id for row in registry.list_projects()] == [existing.id]
    assert app.state.selection.current_id() == SESSION_PROJECT_ID


# --------------------------------------------------------------------------- #
# POST /projects — registering a project
# --------------------------------------------------------------------------- #


def test_adding_a_project_returns_its_row_and_the_listing_then_has_it(tmp_path: Path) -> None:
    # 201 with the created row, not a bodiless Location: the row carries three facts
    # only the server holds — the minted id, the addedAt stamp and the probed condition
    # — so a client handed a URL would have to immediately GET it to render the dropdown
    # entry it just created.
    app = _real_app(tmp_path)
    candidate = _project_dir(tmp_path / "candidate")

    client = TestClient(app)
    response = client.post(_LIST_ROUTE, json={"path": str(candidate)}, headers=AUTH)

    assert response.status_code == 201
    row = response.json()
    assert row["path"] == str(candidate)
    assert row["name"] == "candidate"
    assert row["registered"] is True
    assert row["condition"] == "ok"
    assert row["addedAt"] is not None
    # The published shape is exactly the one the listing publishes.
    assert RegisteredProjectOut.model_validate(row).model_dump(mode="json") == row
    assert _by_id(_list(app)[1])[row["id"]] == row


def test_adding_a_project_does_not_select_it(tmp_path: Path) -> None:
    # Registration and selection are separate acts: conflating them would yank the board
    # out from under an operator adding a second project while reading the first.
    app = _real_app(tmp_path)
    candidate = _project_dir(tmp_path / "candidate")

    response = TestClient(app).post(_LIST_ROUTE, json={"path": str(candidate)}, headers=AUTH)

    assert response.status_code == 201
    assert response.json()["selected"] is False
    assert app.state.selection.current_id() == SESSION_PROJECT_ID
    assert _current(app)[1]["selected"]["id"] == SESSION_PROJECT_ID


def test_a_supplied_name_wins_over_the_directory_name(tmp_path: Path) -> None:
    # The stored name is the user's label and is never re-derived from the path, so a
    # later rename of the directory cannot silently rename the project in the switcher.
    app = _real_app(tmp_path)
    candidate = _project_dir(tmp_path / "candidate")

    response = TestClient(app).post(
        _LIST_ROUTE, json={"path": str(candidate), "name": "My Console"}, headers=AUTH
    )

    assert response.status_code == 201
    assert response.json()["name"] == "My Console"


@pytest.mark.parametrize(
    "path",
    ["relative/project", "", "   "],
    ids=["relative", "blank", "whitespace"],
)
def test_a_path_that_is_not_an_identity_is_invalid_project_path_400(
    path: str, tmp_path: Path
) -> None:
    # A relative path would be resolved against the SERVER's working directory —
    # something the caller, on the other side of an HTTP boundary, cannot see and did
    # not choose — so the row would name a directory nobody asked for. A blank one is
    # the same mistake wearing a different hat: Path("") is Path("."), the cwd again.
    # All three are the one stable code, with the difference carried in the message.
    app = _real_app(tmp_path)

    response = TestClient(app).post(_LIST_ROUTE, json={"path": path}, headers=AUTH)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_project_path"
    # The caller's own input is echoed back; the resolved form never is.
    assert response.json()["error"]["details"]["path"] == path


def test_a_directory_that_is_not_a_project_is_project_not_found_404(tmp_path: Path) -> None:
    # The registry itself would happily hold this row — it records intent, not existence
    # — so the refusal is the ENDPOINT's: registering a directory with no tickets
    # manifest would put a permanently unusable row in the operator's dropdown.
    app = _real_app(tmp_path)
    plain = _project_dir(tmp_path / "plain", manifest=False)

    response = TestClient(app).post(_LIST_ROUTE, json={"path": str(plain)}, headers=AUTH)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_found"
    assert _list(app)[1]["total"] == 1, "the session row only; nothing was registered"


def test_registering_the_same_directory_twice_is_duplicate_project_path_409(
    tmp_path: Path,
) -> None:
    # NOT idempotent, by design: a silent 200 could not be told apart from a fresh add,
    # so the SPA could not say "you already track this" and offer to switch to it. The
    # second spelling is the same directory reached through a `..`, which is what proves
    # the conflict is decided on the CANONICAL path rather than on the literal string.
    app = _real_app(tmp_path)
    candidate = _project_dir(tmp_path / "candidate")
    client = TestClient(app)

    first = client.post(_LIST_ROUTE, json={"path": str(candidate)}, headers=AUTH)
    second = client.post(
        _LIST_ROUTE, json={"path": str(candidate / ".." / "candidate")}, headers=AUTH
    )

    assert first.status_code == 201
    assert second.status_code == 409
    error = second.json()["error"]
    assert error["code"] == "duplicate_project_path"
    # The existing row's id, so the client can offer "switch to it" instead of making
    # the user hunt through the list for a project they cannot re-add.
    assert error["details"]["existingId"] == first.json()["id"]
    assert _list(app)[1]["total"] == 2, "the session row plus exactly one add"


def test_an_unknown_body_field_is_rejected_rather_than_ignored(tmp_path: Path) -> None:
    # extra="forbid" on the request that makes the console open an arbitrary path: a key
    # the server does not understand is far likelier to be a caller sending a field this
    # contract never agreed to than a harmless typo, and dropping it silently would let
    # that caller believe an option took effect.
    app = _real_app(tmp_path)
    candidate = _project_dir(tmp_path / "candidate")

    response = TestClient(app).post(
        _LIST_ROUTE, json={"path": str(candidate), "select": True}, headers=AUTH
    )

    assert response.status_code == 422
    assert _list(app)[1]["total"] == 1, "nothing was registered"


def test_adding_needs_a_registry_to_add_to(tmp_path: Path) -> None:
    # Pinned mode is a valid configuration for the READS (it is every pre-v3 app), but a
    # mutation has no degraded answer available: there is no store to write the row to.
    # That is a wiring fact about this deployment, not something the client can fix, so
    # it fails like the writer seam does — a 500 with a stack trace in the server's log
    # — rather than as a 4xx the SPA would render as user error.
    app = _make_app(_project_dir(tmp_path / "pinned"), adapter=RealFileAdapter())
    candidate = _project_dir(tmp_path / "candidate")

    with pytest.raises(RuntimeError, match="project_registry"):
        TestClient(app).post(_LIST_ROUTE, json={"path": str(candidate)}, headers=AUTH)


# --------------------------------------------------------------------------- #
# DELETE /projects/{project_id} — un-registering a project
# --------------------------------------------------------------------------- #


def test_removing_a_project_drops_it_from_the_listing(tmp_path: Path) -> None:
    # 204 with an empty body: removal deletes one row from the console's OWN table and
    # never touches the project directory, so there is no diff to preview and no
    # artefact to report — unlike the ticket writes.
    registry = FakeProjectRegistry()
    row = registry.add_project(_project_dir(tmp_path / "tracked"))
    app = _real_app(tmp_path, registry)

    response = TestClient(app).delete(f"{_LIST_ROUTE}/{row.id}", headers=AUTH)

    assert response.status_code == 204
    assert response.content == b""
    assert row.id not in _by_id(_list(app)[1])
    assert row.path.is_dir(), "the project's own files are untouched"


def test_removing_an_unknown_id_is_project_not_registered_404(tmp_path: Path) -> None:
    # The port answers False rather than raising, so the 404 is the EDGE's decision. It
    # is the right one: a 204 for an id the console never held would tell the SPA its
    # dropdown is now in a state it is not.
    app = _real_app(tmp_path)
    unknown_id = "0" * 32

    response = TestClient(app).delete(f"{_LIST_ROUTE}/{unknown_id}", headers=AUTH)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_registered"


def test_removing_the_session_row_is_session_project_not_removable_409(tmp_path: Path) -> None:
    # The sentinel is published as a row on every listing, so a client legitimately
    # holds this id — but it was never registered and there is nothing to delete. A 409
    # says so; a 404 would claim the id names nothing, contradicting the listing that
    # just handed it out, and a 422 would call a well-known id malformed.
    app = _real_app(tmp_path)

    response = TestClient(app).delete(f"{_LIST_ROUTE}/{SESSION_PROJECT_ID}", headers=AUTH)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_project_not_removable"
    assert SESSION_PROJECT_ID in _by_id(_list(app)[1]), "the row is still offered"


def test_a_malformed_project_id_is_rejected_at_the_boundary(tmp_path: Path) -> None:
    # The path param admits exactly two forms — 32 hex digits or the sentinel — so an id
    # no row could ever answer to never reaches the store, and because neither form can
    # contain a separator or a dot, an id can never name a parent directory.
    app = _real_app(tmp_path)

    response = TestClient(app).delete(f"{_LIST_ROUTE}/not-an-id", headers=AUTH)

    assert response.status_code == 422


def test_removing_the_selected_project_leaves_nothing_selected(tmp_path: Path) -> None:
    # The persisted selection is cleared by the schema's ON DELETE SET NULL, but the
    # PROCESS-LOCAL one would otherwise outlive its row — and every project-scoped read
    # would then answer selected_project_not_registered instead of the
    # no_project_selected that is actually true. Never a silent fallback to the pin.
    registry = FakeProjectRegistry()
    row = registry.add_project(_project_dir(tmp_path / "tracked"))
    app = _real_app(tmp_path, registry)
    client = TestClient(app)
    assert client.put(_CURRENT_ROUTE, json={"projectId": row.id}, headers=AUTH).status_code == 200

    assert client.delete(f"{_LIST_ROUTE}/{row.id}", headers=AUTH).status_code == 204

    assert _current(app)[1] == {"selected": None, "reason": "no_selection"}
    assert client.get(_PROJECT_ROUTE).json()["error"]["code"] == "no_project_selected"


def test_removing_an_unselected_project_leaves_the_selection_alone(tmp_path: Path) -> None:
    # Clearing on every delete would bump the watcher generation and tear down live
    # updates for every SSE client whenever an operator tidied up an unrelated row.
    registry = FakeProjectRegistry()
    kept = registry.add_project(_project_dir(tmp_path / "kept"))
    doomed = registry.add_project(_project_dir(tmp_path / "doomed"))
    app = _real_app(tmp_path, registry)
    client = TestClient(app)
    client.put(_CURRENT_ROUTE, json={"projectId": kept.id}, headers=AUTH)

    assert client.delete(f"{_LIST_ROUTE}/{doomed.id}", headers=AUTH).status_code == 204

    assert _current(app)[1]["selected"]["id"] == kept.id


# --------------------------------------------------------------------------- #
# PUT /projects/current — switching project
# --------------------------------------------------------------------------- #


def test_selecting_a_project_moves_what_every_endpoint_answers(tmp_path: Path) -> None:
    # The switch answers the SAME envelope GET /projects/current does, built from the
    # same resolution, so the SPA can feed the response straight into the header it
    # would otherwise refetch — and the move is visible to the project-scoped reads too,
    # which is the whole point of gating this route.
    registry = FakeProjectRegistry()
    row = registry.add_project(_project_dir(tmp_path / "chosen"))
    app = _real_app(tmp_path, registry)
    client = TestClient(app)

    response = client.put(_CURRENT_ROUTE, json={"projectId": row.id}, headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "selected": {
            "id": row.id,
            "name": row.name,
            "path": str(row.path),
            "addedAt": response.json()["selected"]["addedAt"],
            "registered": True,
            "selected": True,
            "condition": "ok",
        },
        "reason": None,
    }
    assert _current(app)[1] == response.json()
    assert _by_id(_list(app)[1])[row.id]["selected"] is True
    assert client.get(_PROJECT_ROUTE).json()["rootPath"] == str(row.path)


def test_selecting_an_unknown_id_is_project_not_registered_404(tmp_path: Path) -> None:
    # The one write the port makes fail loudly rather than succeed at pointing the whole
    # console at nothing — and the selection must not have moved on the way.
    registry = FakeProjectRegistry()
    row = registry.add_project(_project_dir(tmp_path / "chosen"))
    app = _real_app(tmp_path, registry)
    client = TestClient(app)
    client.put(_CURRENT_ROUTE, json={"projectId": row.id}, headers=AUTH)
    unknown_id = "0" * 32

    response = client.put(_CURRENT_ROUTE, json={"projectId": unknown_id}, headers=AUTH)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_registered"
    assert _current(app)[1]["selected"]["id"] == row.id, "the failed switch changed nothing"


def test_selecting_the_session_row_switches_back_to_the_pinned_root(tmp_path: Path) -> None:
    # A `factory-console PATH` boot must be able to switch BACK to the path the operator
    # typed, so the reserved id is an ordinary target whenever a pin exists — and it is
    # the ONLY selectable target in pinned mode, which is why it needs no registry.
    registry = FakeProjectRegistry()
    row = registry.add_project(_project_dir(tmp_path / "chosen"))
    app = _real_app(tmp_path, registry)
    client = TestClient(app)
    client.put(_CURRENT_ROUTE, json={"projectId": row.id}, headers=AUTH)

    response = client.put(_CURRENT_ROUTE, json={"projectId": SESSION_PROJECT_ID}, headers=AUTH)

    assert response.status_code == 200
    assert response.json()["selected"]["id"] == SESSION_PROJECT_ID
    assert response.json()["selected"]["registered"] is False
    assert app.state.selection.current_id() == SESSION_PROJECT_ID


def test_selecting_the_session_row_without_a_pin_is_project_not_registered_404(
    tmp_path: Path,
) -> None:
    # With no pin the sentinel names no directory at all, so accepting it would move the
    # session onto nothing — the same "succeeded at selecting nothing" outcome an
    # unknown registry id is refused for, reached by the one path the registry cannot
    # see. Same statement to the caller, so the same code.
    registry = FakeProjectRegistry()
    app = _real_app(tmp_path, registry)
    app.state.selection = SelectionState(pinned_root=None, registry=registry)

    response = TestClient(app).put(
        _CURRENT_ROUTE, json={"projectId": SESSION_PROJECT_ID}, headers=AUTH
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_registered"
    assert _current(app)[1] == {"selected": None, "reason": "no_selection"}


def test_selecting_a_project_whose_path_is_gone_succeeds_and_names_the_reason(
    tmp_path: Path,
) -> None:
    # A degraded condition is explicitly NOT a precondition for selecting: selecting a
    # project whose directory has been deleted is exactly what an operator does in order
    # to then remove the row, so the switch must succeed and the CONSEQUENCE be named —
    # rather than the switch failing opaquely and stranding them on the project they
    # were trying to leave.
    registry = FakeProjectRegistry()
    (tmp_path / "gone").mkdir()
    row = registry.add_project(tmp_path / "gone")
    app = _real_app(tmp_path, registry)
    row.path.rmdir()

    client = TestClient(app)
    response = client.put(_CURRENT_ROUTE, json={"projectId": row.id}, headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"selected": None, "reason": "selected_project_missing"}
    # The selection really did move, and the project-scoped reads now refuse with the
    # named 409 rather than falling back to the pinned root.
    assert app.state.selection.current_id() == row.id
    refused = client.get(_PROJECT_ROUTE)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "selected_project_unavailable"
    # Which is what makes the row removable from the state it is now in.
    assert client.delete(f"{_LIST_ROUTE}/{row.id}", headers=AUTH).status_code == 204


def test_selecting_needs_a_registry_for_a_registered_id(tmp_path: Path) -> None:
    # Pinned mode can never name another project, so an id here has no store to be
    # looked up in; accepting it would move the session onto an id nothing answers to.
    # Same wiring-bug verdict as the add route.
    app = _make_app(_project_dir(tmp_path / "pinned"))
    unknown_id = "0" * 32

    with pytest.raises(RuntimeError, match="project_registry"):
        TestClient(app).put(_CURRENT_ROUTE, json={"projectId": unknown_id}, headers=AUTH)
