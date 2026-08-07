"""Integration tests for ``POST``/``PUT``/``DELETE`` ``/api/v1/tickets`` (T65).

Drive the real ``create_app`` over ``httpx.AsyncClient`` + ``ASGITransport`` (the repo
runs ``asyncio_mode=auto``, so ``async def test_...`` needs no decorator) against two
wirings, each chosen for what it can actually prove:

* the **filesystem pair** — :class:`RealFileAdapter` + :class:`RealFileWriter` over a
  ``tmp_path`` copy of the checked-in ``with_run_state`` fixture (3 ``todo`` tickets, 3
  non-``todo``) — for every assertion that needs the write and read ports to agree.
  ``GET /tickets/{id}`` must observe what a ``POST``/``PUT``/``DELETE`` wrote, and
  ``?dryRun=true`` must leave the project byte-for-byte untouched; both are claims about
  ONE shared project, which the shipped :class:`FakeFileWriter` and
  :class:`FakeFileAdapter` cannot make (they hold SEPARATE in-memory state — see the
  fixture note in ``tests/unit/test_write_service.py``). The fixture's run-states also
  make the EDIT mutability gate (``todo``/``unknown``) real rather than seeded.
* the **in-memory pair** — :class:`FakeFileAdapter` + :class:`FakeFileWriter` — for the
  assertions that never reach a port at all: the write-token 401s, the
  ``invalid_ticket_id`` 400 rejected at the ``Path`` boundary, the frozen OpenAPI shape,
  and the proof that read routes stayed header-free.

Every failure assertion pins the envelope's ``error.code``, not just the status number,
since that code is what the SPA branches on.

Since v3.0 all three handlers resolve their root through the selection seam, so the
filesystem pair also carries the cases that decide the two orderings this module exists
to get right: an unresolvable selection REFUSES with the named 409 while the pinned tree
comes out byte-identical (never a fall-back write into the wrong repo); an unauthenticated
caller with no selection gets the 401, not the 409, so the token check demonstrably runs
first and leaks nothing about project state; and — the fail-open regression test — a write
against a SECOND, selected on-disk project lands in that tree while the first, pinned one
is untouched. That last case needs two real fixture copies under ONE app, which is why it
builds the app with ``app_over(first, registry=...)`` rather than ``real_app``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from _write_support import (
    AUTH,
    PINNED_TOKEN,
    WRONG_TOKEN,
)
from _write_support import (
    app_over as _app_over,
)
from _write_support import (
    client as _client,
)
from _write_support import (
    fixture_copy as _fixture_copy,
)
from _write_support import (
    real_app as _real_app,
)
from fastapi import FastAPI
from fastapi.dependencies.utils import get_flat_params
from fastapi.params import Query
from fastapi.routing import APIRoute
from httpx import AsyncClient

from factory_console.api.v1.tickets_write import _ALLOWED_QUERY_KEYS
from factory_console.api.v1.tickets_write import router as write_router
from factory_console.api.write_token import WRITE_TOKEN_SCHEME_NAME
from factory_console.app import create_app
from factory_console.config import WRITE_TOKEN_HEADER
from factory_console.domain import Project
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.fake_writer import FakeFileWriter
from factory_console.logging import _LOG_FORMAT
from factory_console.services.project_selection import SelectionState
from factory_console.store.fake_registry import FakeProjectRegistry

# The fixture's run-states: only ``todo`` ids are editable (write_gate.MUTABLE_STATES).
TODO_ID = "CAD-131"
DELETABLE_TODO_ID = "CAD-152"
NON_TODO_IDS = ["CAD-100", "CAD-118", "CAD-125"]
# Used by the T92 case only, which MOVES this id's marker out of ``todo/`` and into a
# state directory this console has no name for — so it is named ONLY under that
# directory, the shape that used to resolve ``absent`` and delete cleanly.
ONLY_UNKNOWN_STATE_ID = "CAD-140"
NEW_ID = "CAD-210"

# Outside TICKET_ID_PATTERN, so the ``Path`` validator rejects it before any handler.
INVALID_ID = "bad$id"

# The three verbs this module gates, guards and resolves. Named once so a case that must
# hold for ALL of them cannot silently cover only two.
WRITE_VERBS = ["POST", "PUT", "DELETE"]


def _draft_body(ticket_id: str = NEW_ID, **overrides: Any) -> dict[str, Any]:
    """A valid ``TicketDraft`` request body (``extra='forbid'``, so no stray keys)."""
    body: dict[str, Any] = {
        "id": ticket_id,
        "title": "Team analytics dashboard",
        "track": "frontend",
        "milestone": "v2",
        "dependsOn": [DELETABLE_TODO_ID],
        "provides": "Participation and consistency across a team",
        "files": ["frontend/src/routes/team/+page.svelte"],
        "bodyMarkdown": "# Team analytics\n\nDashboard body.\n",
    }
    body.update(overrides)
    return body


def _edit_body(**overrides: Any) -> dict[str, Any]:
    """A valid ``TicketEdit`` request body (``TicketDraft`` minus ``id``)."""
    body: dict[str, Any] = {
        "title": "Weekly digest email (revised)",
        "track": "notifications",
        "milestone": "v1",
        "dependsOn": [],
        "provides": "Monday-morning digest, rewritten",
        "files": ["server/cadence/notifications/weekly_digest.py"],
        "bodyMarkdown": "# Weekly digest\n\nRewritten body.\n",
    }
    body.update(overrides)
    return body


def _fake_app() -> FastAPI:
    """Build the app over the in-memory pair, for the paths that reach no port."""
    project = Project(
        rootPath=Path("/factory/demo-project"),
        ticketsManifestPath=Path("/factory/demo-project/docs/planning/tickets.json"),
        ticketsDir=Path("/factory/demo-project/docs/planning/tickets"),
        discoveredAt=datetime(2026, 7, 25, 12, 0, 0),
    )
    return create_app(
        FakeFileAdapter(project=project, tickets=[]),
        version="0.0.0",
        project_root=project.rootPath,
        file_writer=FakeFileWriter(manifest=[]),
        write_token=PINNED_TOKEN,
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    """Map every file under ``root`` (root-relative POSIX) to its exact bytes.

    A dry-run must write NOTHING, so the comparison is over the whole project tree
    rather than the three files a write touches: that also catches a stray temp file the
    atomic writer might leave behind, which a per-file check would miss.
    """
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _diff_texts(body: dict[str, Any]) -> list[str]:
    """The unified-diff text of every file in a ``WriteResult``'s ``DiffPreview``."""
    return [file["diff"] for file in body["diff"]["files"]]


def _assert_applied_with_diff(body: dict[str, Any], ticket_id: str) -> None:
    """Assert a ``WriteResult`` reports an apply carrying a real, non-empty diff."""
    assert body["applied"] is True
    assert body["ticketId"] == ticket_id
    assert body["changedFiles"]
    texts = _diff_texts(body)
    assert texts
    assert all(text for text in texts)
    # changedFiles and the diff describe the same write, so they always agree.
    assert body["changedFiles"] == [file["path"] for file in body["diff"]["files"]]


def _assert_previewed_with_diff(body: dict[str, Any], ticket_id: str) -> None:
    """Assert a ``WriteResult`` reports a dry-run: a diff, no apply, and no ticket."""
    assert body["applied"] is False
    assert body["ticket"] is None
    assert body["ticketId"] == ticket_id
    texts = _diff_texts(body)
    assert texts
    assert all(text for text in texts)


# --------------------------------------------------------------------------- #
# Apply — the write lands and GET /tickets/{id} observes it
# --------------------------------------------------------------------------- #


async def test_create_applies_with_201_and_is_observable_via_get(tmp_path: Path) -> None:
    app, _root = _real_app(tmp_path)
    async with _client(app) as client:
        resp = await client.post("/api/v1/tickets", json=_draft_body(), headers=AUTH)
        # 201: an apply created a resource (the dry-run twin below answers 200).
        assert resp.status_code == 201
        body = resp.json()
        _assert_applied_with_diff(body, NEW_ID)
        assert body["ticket"]["title"] == "Team analytics dashboard"

        detail = await client.get(f"/api/v1/tickets/{NEW_ID}")
        assert detail.status_code == 200
        assert detail.json()["title"] == "Team analytics dashboard"


async def test_edit_on_todo_ticket_applies_and_is_observable_via_get(tmp_path: Path) -> None:
    app, _root = _real_app(tmp_path)
    async with _client(app) as client:
        resp = await client.put(f"/api/v1/tickets/{TODO_ID}", json=_edit_body(), headers=AUTH)
        assert resp.status_code == 200
        _assert_applied_with_diff(resp.json(), TODO_ID)

        detail = (await client.get(f"/api/v1/tickets/{TODO_ID}")).json()
        assert detail["title"] == "Weekly digest email (revised)"
        assert "Rewritten body." in detail["bodyMarkdown"]


async def test_delete_on_todo_ticket_applies_and_the_ticket_is_gone(tmp_path: Path) -> None:
    app, _root = _real_app(tmp_path)
    async with _client(app) as client:
        resp = await client.delete(f"/api/v1/tickets/{DELETABLE_TODO_ID}", headers=AUTH)
        assert resp.status_code == 200
        _assert_applied_with_diff(resp.json(), DELETABLE_TODO_ID)

        detail = await client.get(f"/api/v1/tickets/{DELETABLE_TODO_ID}")
        assert detail.status_code == 404
        assert detail.json()["error"]["code"] == "ticket_not_found"


# --------------------------------------------------------------------------- #
# ?dryRun=true — a diff, no apply, and provably nothing written
# --------------------------------------------------------------------------- #


async def test_create_dry_run_returns_200_and_writes_nothing(tmp_path: Path) -> None:
    app, root = _real_app(tmp_path)
    before = _snapshot(root)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/tickets", params={"dryRun": "true"}, json=_draft_body(), headers=AUTH
        )
        # 200, not 201: a preview created nothing, so it must not claim it did.
        assert resp.status_code == 200
        _assert_previewed_with_diff(resp.json(), NEW_ID)

        assert (await client.get(f"/api/v1/tickets/{NEW_ID}")).status_code == 404
    assert _snapshot(root) == before


async def test_edit_dry_run_previews_and_writes_nothing(tmp_path: Path) -> None:
    app, root = _real_app(tmp_path)
    before = _snapshot(root)
    async with _client(app) as client:
        resp = await client.put(
            f"/api/v1/tickets/{TODO_ID}",
            params={"dryRun": "true"},
            json=_edit_body(),
            headers=AUTH,
        )
        assert resp.status_code == 200
        _assert_previewed_with_diff(resp.json(), TODO_ID)

        detail = (await client.get(f"/api/v1/tickets/{TODO_ID}")).json()
        assert detail["title"] == "Weekly digest email"
    assert _snapshot(root) == before


async def test_delete_dry_run_previews_and_writes_nothing(tmp_path: Path) -> None:
    app, root = _real_app(tmp_path)
    before = _snapshot(root)
    async with _client(app) as client:
        resp = await client.delete(
            f"/api/v1/tickets/{DELETABLE_TODO_ID}", params={"dryRun": "true"}, headers=AUTH
        )
        assert resp.status_code == 200
        _assert_previewed_with_diff(resp.json(), DELETABLE_TODO_ID)

        assert (await client.get(f"/api/v1/tickets/{DELETABLE_TODO_ID}")).status_code == 200
    assert _snapshot(root) == before


async def test_the_audit_line_separates_an_applied_write_from_a_dry_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The access log formats ``request.url.path``, which drops the query string, and a
    # delete answers 200 whichever path it took — so those two lines are byte-identical
    # for a preview that changed nothing and an apply that removed the ticket file and
    # rewrote the manifest. The audit record is what tells them apart afterwards.
    app, _ = _real_app(tmp_path)
    with caplog.at_level("INFO", logger="factory_console.api.v1.tickets_write"):
        async with _client(app) as client:
            await client.delete(
                f"/api/v1/tickets/{DELETABLE_TODO_ID}", params={"dryRun": "true"}, headers=AUTH
            )
            await client.delete(f"/api/v1/tickets/{DELETABLE_TODO_ID}", headers=AUTH)

    records = [
        record for record in caplog.records if record.name == "factory_console.api.v1.tickets_write"
    ]
    assert len(records) == 2
    previewed, applied = records

    # Assert on the RENDERED message, not on ``LogRecord`` attributes: the app installs
    # one message-only formatter, so attributes attached via ``extra=`` exist on the
    # record (and would satisfy an attribute assertion) while never reaching the
    # operator. Only what ``format()`` emits is real.
    formatter = logging.Formatter(_LOG_FORMAT)
    previewed_line = formatter.format(previewed)
    applied_line = formatter.format(applied)

    # The two lines must not be byte-identical — that is the whole point of the record.
    assert previewed_line != applied_line
    assert "dry-run" in previewed_line
    assert "applied" in applied_line
    for line in (previewed_line, applied_line):
        assert "delete" in line
        assert DELETABLE_TODO_ID in line
    # The apply names the files it actually wrote — the point of keeping the record.
    assert "docs/planning/tickets.json" in applied_line
    # An audit trail records what changed, never the secret that authorized it.
    assert PINNED_TOKEN not in caplog.text
    assert PINNED_TOKEN not in applied_line


@pytest.mark.parametrize("misspelling", ["dryrun", "dry_run", "dryRunn", "DRYRUN"])
async def test_a_misspelled_dry_run_flag_is_rejected_and_writes_nothing(
    misspelling: str, tmp_path: Path
) -> None:
    # The flag that separates a preview from an irreversible delete must fail CLOSED:
    # an unrecognized query key is a 400, never a silent apply of the real thing.
    app, root = _real_app(tmp_path)
    before = _snapshot(root)
    async with _client(app) as client:
        resp = await client.delete(
            f"/api/v1/tickets/{DELETABLE_TODO_ID}", params={misspelling: "true"}, headers=AUTH
        )
        assert resp.status_code == 400, misspelling
        assert resp.json()["error"]["code"] == "unknown_query_param", misspelling

        assert (await client.get(f"/api/v1/tickets/{DELETABLE_TODO_ID}")).status_code == 200
    assert _snapshot(root) == before


@pytest.mark.parametrize("query", ["dryRun=true&dryRun=false", "dryRun=false&dryRun=true"])
async def test_a_repeated_dry_run_flag_is_rejected_and_writes_nothing(
    query: str, tmp_path: Path
) -> None:
    # A repeated key carries the ALLOWED name, so an allow-list over the key SET sees
    # nothing wrong — while FastAPI binds a scalar bool last-wins. Without this guard
    # `?dryRun=true&dryRun=false` deletes the ticket the caller asked to preview, so the
    # duplicate must be rejected exactly like a misspelling.
    app, root = _real_app(tmp_path)
    before = _snapshot(root)
    async with _client(app) as client:
        resp = await client.delete(f"/api/v1/tickets/{DELETABLE_TODO_ID}?{query}", headers=AUTH)
        assert resp.status_code == 400, query
        assert resp.json()["error"]["code"] == "repeated_query_param", query
        assert resp.json()["error"]["details"]["repeated"] == ["dryRun"], query

        # The ticket is still there — the preview did not become an apply.
        assert (await client.get(f"/api/v1/tickets/{DELETABLE_TODO_ID}")).status_code == 200
    assert _snapshot(root) == before


async def test_a_repeated_dry_run_flag_is_rejected_on_create_and_edit(tmp_path: Path) -> None:
    # The guard sits on the router, so all three verbs inherit it — a repeated flag must
    # not let a POST create or a PUT overwrite while claiming to preview.
    app, root = _real_app(tmp_path)
    before = _snapshot(root)
    async with _client(app) as client:
        created = await client.post(
            "/api/v1/tickets?dryRun=true&dryRun=false", json=_draft_body(), headers=AUTH
        )
        assert created.status_code == 400
        assert created.json()["error"]["code"] == "repeated_query_param"

        edited = await client.put(
            f"/api/v1/tickets/{DELETABLE_TODO_ID}?dryRun=true&dryRun=false",
            json=_edit_body(),
            headers=AUTH,
        )
        assert edited.status_code == 400
        assert edited.json()["error"]["code"] == "repeated_query_param"
    assert _snapshot(root) == before


def test_the_query_allow_list_matches_what_the_routes_declare() -> None:
    # Attaching the guard at the router makes it impossible to forget on a fourth verb,
    # but `_ALLOWED_QUERY_KEYS` is still hand-maintained — so a query param added to any
    # of these routes would be refused as `unknown_query_param` even though the route
    # declares it and OpenAPI publishes it. Pin the two together so that drift fails HERE
    # instead of at runtime. Flattened so a param declared by a dependency counts too.
    declared = {
        param.alias
        for route in write_router.routes
        if isinstance(route, APIRoute)
        for param in get_flat_params(route.dependant)
        if isinstance(param.field_info, Query)
    }
    assert declared == set(_ALLOWED_QUERY_KEYS)


async def test_a_single_dry_run_flag_still_previews(tmp_path: Path) -> None:
    # The duplicate guard must not reject the ordinary one-flag preview it protects.
    app, root = _real_app(tmp_path)
    before = _snapshot(root)
    async with _client(app) as client:
        resp = await client.delete(f"/api/v1/tickets/{DELETABLE_TODO_ID}?dryRun=true", headers=AUTH)
        assert resp.status_code == 200
        _assert_previewed_with_diff(resp.json(), DELETABLE_TODO_ID)
    assert _snapshot(root) == before


async def test_an_unknown_query_param_does_not_mask_a_missing_token(tmp_path: Path) -> None:
    # The token guard is listed first, so an unauthorized caller still learns only that
    # the token was rejected — the query guard cannot leak that the route exists.
    app, _ = _real_app(tmp_path)
    async with _client(app) as client:
        resp = await client.post("/api/v1/tickets", params={"dryrun": "true"}, json=_draft_body())
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "write_token_invalid"


# --------------------------------------------------------------------------- #
# The EDIT mutability gate (``todo``/``unknown``) + the create-collision guard (409s)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ticket_id", NON_TODO_IDS)
async def test_edit_on_non_todo_ticket_is_ticket_not_mutable_409(
    ticket_id: str, tmp_path: Path
) -> None:
    app, root = _real_app(tmp_path)
    before = _snapshot(root)
    async with _client(app) as client:
        resp = await client.put(f"/api/v1/tickets/{ticket_id}", json=_edit_body(), headers=AUTH)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ticket_not_mutable"
    # The gate fires before any write, so an in-flight/ready/merged ticket is untouched.
    assert _snapshot(root) == before


@pytest.mark.parametrize("ticket_id", NON_TODO_IDS)
async def test_delete_on_non_todo_ticket_is_ticket_not_mutable_409(
    ticket_id: str, tmp_path: Path
) -> None:
    app, root = _real_app(tmp_path)
    before = _snapshot(root)
    async with _client(app) as client:
        resp = await client.delete(f"/api/v1/tickets/{ticket_id}", headers=AUTH)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ticket_not_mutable"
    assert _snapshot(root) == before


async def test_edit_on_json_sourced_merged_ticket_is_ticket_not_mutable_409(
    tmp_path: Path,
) -> None:
    # The end-to-end proof T78 exists for: a project whose run-state comes from
    # the factory's real ``.factory/run-state.json`` (not the legacy marker
    # directory) refuses an edit to a ticket the factory recorded ``merged``.
    # Pre-T78 this request succeeded (the JSON was never read, so the ticket
    # resolved the mutable ``unknown``).
    app, root = _real_app(tmp_path)
    (root / ".factory" / "run-state.json").write_text(
        '{"version": 1, "tickets": {"CAD-131": {"status": "merged", "pr_url": null}}}',
        encoding="utf-8",
    )
    before = _snapshot(root)
    async with _client(app) as client:
        resp = await client.put("/api/v1/tickets/CAD-131", json=_edit_body(), headers=AUTH)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ticket_not_mutable"
    assert _snapshot(root) == before


async def test_edit_on_a_ticket_the_json_source_does_not_list_is_absent_409(
    tmp_path: Path,
) -> None:
    # T80's end-to-end proof, the counterpart of the `merged` case above: the JSON
    # source resolves and simply has no entry for CAD-131, so the gate answers
    # RunState.absent — refused, where pre-T80 it resolved the mutable `unknown` and
    # this PUT succeeded. Asserted through the real app because the `absent` branch
    # of TicketNotMutable (and the source path it puts in `message`) was otherwise
    # only ever exercised at the exception-construction level.
    app, root = _real_app(tmp_path)
    run_state_json = root / ".factory" / "run-state.json"
    run_state_json.write_text(
        '{"version": 1, "tickets": {"CAD-100": {"status": "merged", "pr_url": null}}}',
        encoding="utf-8",
    )
    before = _snapshot(root)
    async with _client(app) as client:
        resp = await client.put("/api/v1/tickets/CAD-131", json=_edit_body(), headers=AUTH)

    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "ticket_not_mutable"
    assert error["details"] == {"ticketId": "CAD-131", "runState": "absent"}
    # The operator needs to know WHICH file was consulted — the whole point of the
    # distinct `absent` wording is that the answer is "the file you are not looking
    # at" (T80 step 4).
    assert str(run_state_json) in error["message"]
    assert _snapshot(root) == before


async def test_a_status_this_console_cannot_classify_refuses_both_writes_end_to_end(
    tmp_path: Path,
) -> None:
    # T80 amendment 4 through the real app, and the amendment's own worked example:
    # the factory gains a tenth FAC_STATES member (`in_review`), this console does not
    # know the name, and CAD-131 is a ticket a lane is actively reviewing. Pre-amendment
    # the unrecognised status resolved the mutable `unknown` and this PUT SUCCEEDED —
    # the console editing a ticket the factory was working on, which is the fail-open
    # the ticket exists to close, arriving through the one door left open.
    #
    # Asserted end to end rather than at the resolver because the whole point is the
    # gate's answer: the state that reaches `MUTABLE_STATES`, the 409 an operator
    # actually sees, and (unlike `absent`) the DELETE being refused too.
    app, root = _real_app(tmp_path)
    run_state_json = root / ".factory" / "run-state.json"
    run_state_json.write_text(
        '{"version": 1, "tickets": {"CAD-131": {"status": "in_review", "pr_url": null}}}',
        encoding="utf-8",
    )
    before = _snapshot(root)
    async with _client(app) as client:
        edited = await client.put("/api/v1/tickets/CAD-131", json=_edit_body(), headers=AUTH)
        deleted = await client.delete("/api/v1/tickets/CAD-131", headers=AUTH)

    for resp in (edited, deleted):
        assert resp.status_code == 409
        error = resp.json()["error"]
        assert error["code"] == "ticket_not_mutable"
        assert error["details"] == {"ticketId": "CAD-131", "runState": "unreadable"}
        # The refusal NAMES the value and the file (amendment 4, step 1): "not tracked"
        # would send an operator hunting a missing entry that is right there, and the
        # `unreadable` sibling's "check its permissions" would send them to chmod a
        # file that reads perfectly well. The fix here is a console that knows the
        # status the factory now writes.
        assert "in_review" in error["message"]
        assert str(run_state_json) in error["message"]
    assert _snapshot(root) == before


async def test_a_state_directory_this_console_cannot_name_refuses_the_delete_end_to_end(
    tmp_path: Path,
) -> None:
    # T92 through the real app, and DELETE is the request that has to be asserted: the
    # fixture's `.factory/run-state` names CAD-152 under `todo/`, so before this change
    # a factory that also wrote `in_review/CAD-152` — a tenth FAC_STATES entry the
    # console has no name for — left the console reading `todo`, the MUTABLE state, and
    # this DELETE returned 200 on a ticket a lane was actively reviewing. The id named
    # ONLY under `in_review/` is worse still: it resolved `absent`, which is in
    # DELETABLE_STATES, so the delete succeeded with no marker to contradict it.
    #
    # Both shapes are asserted here rather than at the resolver because the whole claim
    # is about the gate's answer: the 409 an operator sees, the file still on disk, and
    # the refusal naming the directory rather than saying "not tracked".
    app, root = _real_app(tmp_path)
    run_state_dir = root / ".factory" / "run-state"
    (run_state_dir / "in_review").mkdir()
    # CAD-152 is `todo` in the fixture: the unknown state must OUTRANK that marker.
    (run_state_dir / "in_review" / DELETABLE_TODO_ID).write_text("", encoding="utf-8")
    # CAD-140 is moved from `todo/` to `in_review/`, so it is named ONLY under the state
    # this console cannot name — the shape that resolved `absent` and deleted cleanly.
    (run_state_dir / "todo" / ONLY_UNKNOWN_STATE_ID).unlink()
    (run_state_dir / "in_review" / ONLY_UNKNOWN_STATE_ID).write_text("", encoding="utf-8")
    before = _snapshot(root)
    async with _client(app) as client:
        deleted = await client.delete(f"/api/v1/tickets/{DELETABLE_TODO_ID}", headers=AUTH)
        edited = await client.put(
            f"/api/v1/tickets/{DELETABLE_TODO_ID}", json=_edit_body(), headers=AUTH
        )
        deleted_unlisted = await client.delete(
            f"/api/v1/tickets/{ONLY_UNKNOWN_STATE_ID}", headers=AUTH
        )

    for ticket_id, resp in (
        (DELETABLE_TODO_ID, deleted),
        (DELETABLE_TODO_ID, edited),
        (ONLY_UNKNOWN_STATE_ID, deleted_unlisted),
    ):
        assert resp.status_code == 409
        error = resp.json()["error"]
        assert error["code"] == "ticket_not_mutable"
        assert error["details"] == {"ticketId": ticket_id, "runState": "unreadable"}
        assert "state 'in_review'" in error["message"]
        assert str(run_state_dir) in error["message"]
    # Nothing was written — in particular the ticket file is still there, which is the
    # concrete loss this refusal prevents.
    assert _snapshot(root) == before


async def test_a_created_ticket_can_be_deleted_but_not_edited_end_to_end(
    tmp_path: Path,
) -> None:
    # T80's amendment through the real app: `create` is ungated, so the id it mints
    # resolves `absent` against the fixture's populated run-state source. Editing it
    # is refused (the rule holds) while DELETING it succeeds (gap 2) — otherwise a
    # mistyped new ticket would be unrecoverable through the UI that created it.
    app, root = _real_app(tmp_path)
    async with _client(app) as client:
        created = await client.post("/api/v1/tickets", json=_draft_body(), headers=AUTH)
        assert created.status_code == 201

        edited = await client.put(f"/api/v1/tickets/{NEW_ID}", json=_edit_body(), headers=AUTH)
        assert edited.status_code == 409
        error = edited.json()["error"]
        assert error["code"] == "ticket_not_mutable"
        assert error["details"] == {"ticketId": NEW_ID, "runState": "absent"}

        deleted = await client.delete(f"/api/v1/tickets/{NEW_ID}", headers=AUTH)
        assert deleted.status_code == 200

        gone = await client.get(f"/api/v1/tickets/{NEW_ID}")
    assert gone.status_code == 404
    assert not (root / "docs" / "planning" / "tickets" / f"{NEW_ID}.md").exists()


@pytest.mark.parametrize("ticket_id", NON_TODO_IDS)
async def test_dry_run_still_previews_a_non_todo_ticket_and_writes_nothing(
    ticket_id: str, tmp_path: Path
) -> None:
    # The asymmetry the two tests above and the one below make it easy to assume away:
    # the mutability gate lives in the WRITER, so it guards the apply only. A dry-run
    # never reaches the writer's gated methods, so previewing an in-flight/ready/merged
    # ticket answers 200 with the diff — the 409 arrives when the SPA applies it. Pinned
    # here (unlike write_conflict, which rejects both paths) so a change in either
    # direction is a deliberate contract change, not a silent one.
    app, root = _real_app(tmp_path)
    before = _snapshot(root)
    async with _client(app) as client:
        edit = await client.put(
            f"/api/v1/tickets/{ticket_id}",
            params={"dryRun": "true"},
            json=_edit_body(),
            headers=AUTH,
        )
        delete = await client.delete(
            f"/api/v1/tickets/{ticket_id}", params={"dryRun": "true"}, headers=AUTH
        )
    for resp in (edit, delete):
        assert resp.status_code == 200
        _assert_previewed_with_diff(resp.json(), ticket_id)
    # Whatever the run-state, a preview writes nothing.
    assert _snapshot(root) == before


@pytest.mark.parametrize("dry_run", ["false", "true"])
async def test_create_on_an_existing_id_is_write_conflict_409(dry_run: str, tmp_path: Path) -> None:
    # Both paths reject: previewing a create for an id that already exists would be a
    # misleading preview, so WriteService guards before the writer runs either way.
    app, _root = _real_app(tmp_path)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/tickets",
            params={"dryRun": dry_run},
            json=_draft_body(ticket_id=TODO_ID),
            headers=AUTH,
        )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "write_conflict"


async def test_edit_unknown_id_is_ticket_not_found_404(tmp_path: Path) -> None:
    app, _root = _real_app(tmp_path)
    async with _client(app) as client:
        resp = await client.put("/api/v1/tickets/CAD-999", json=_edit_body(), headers=AUTH)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ticket_not_found"


async def test_delete_unknown_id_is_ticket_not_found_404(tmp_path: Path) -> None:
    app, _root = _real_app(tmp_path)
    async with _client(app) as client:
        resp = await client.delete("/api/v1/tickets/CAD-999", headers=AUTH)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ticket_not_found"


# --------------------------------------------------------------------------- #
# The write-token guard covers all three verbs (in-memory pair — no port reached)
# --------------------------------------------------------------------------- #


async def _call_write_verb(client: AsyncClient, verb: str, headers: dict[str, str]) -> Any:
    """Issue a well-formed request for ``verb`` so only the token can decide the outcome."""
    if verb == "POST":
        return await client.post("/api/v1/tickets", json=_draft_body(), headers=headers)
    if verb == "PUT":
        return await client.put(f"/api/v1/tickets/{TODO_ID}", json=_edit_body(), headers=headers)
    return await client.delete(f"/api/v1/tickets/{TODO_ID}", headers=headers)


@pytest.mark.parametrize("verb", WRITE_VERBS)
@pytest.mark.parametrize(
    ("case", "headers"),
    [("missing", {}), ("wrong", {WRITE_TOKEN_HEADER: WRONG_TOKEN})],
    ids=["missing-token", "wrong-token"],
)
async def test_every_write_verb_rejects_a_bad_token_as_401(
    verb: str, case: str, headers: dict[str, str]
) -> None:
    # The router-level dependency gates all three verbs, so no verb can be forgotten.
    async with _client(_fake_app()) as client:
        resp = await _call_write_verb(client, verb, headers)
    assert resp.status_code == 401, f"{verb}/{case}"
    error = resp.json()["error"]
    assert error["code"] == "write_token_invalid", f"{verb}/{case}"
    # Neither the expected secret nor the supplied guess may appear in the response.
    assert PINNED_TOKEN not in resp.text
    assert WRONG_TOKEN not in resp.text


@pytest.mark.parametrize("verb", WRITE_VERBS)
async def test_invalid_ticket_id_is_rejected_as_400(verb: str) -> None:
    # A valid token, so the 400 is the pattern rejection and not a 401 — the id never
    # reaches the adapter or the writer. POST is here because a create carries its id in
    # the BODY, not the path: one user mistake must yield one envelope across all three
    # verbs, or the SPA cannot branch on error.code alone.
    async with _client(_fake_app()) as client:
        if verb == "POST":
            resp = await client.post(
                "/api/v1/tickets", json=_draft_body(ticket_id=INVALID_ID), headers=AUTH
            )
        elif verb == "PUT":
            resp = await client.put(
                f"/api/v1/tickets/{INVALID_ID}", json=_edit_body(), headers=AUTH
            )
        else:
            resp = await client.delete(f"/api/v1/tickets/{INVALID_ID}", headers=AUTH)
    assert resp.status_code == 400, verb
    assert resp.json()["error"]["code"] == "invalid_ticket_id", verb


@pytest.mark.parametrize(
    "headers", [{}, {WRITE_TOKEN_HEADER: WRONG_TOKEN}], ids=["no-header", "wrong-header"]
)
async def test_read_ticket_routes_stay_token_free(headers: dict[str, str], tmp_path: Path) -> None:
    # The guard lives on the write router alone: adding it must not have leaked onto the
    # GET routes that share the /tickets path, and a bogus header stays ignored there.
    app, _root = _real_app(tmp_path)
    async with _client(app) as client:
        for read_path in ("/api/v1/tickets", f"/api/v1/tickets/{TODO_ID}"):
            resp = await client.get(read_path, headers=headers)
            assert resp.status_code == 200, read_path


# --------------------------------------------------------------------------- #
# The selection seam: the write goes to the SELECTED project, or nowhere at all
# --------------------------------------------------------------------------- #

# The ticket each APPLYING write below targets, per verb: create mints a fresh id, edit
# rewrites a ``todo`` ticket, delete removes the ``todo`` ticket nothing depends on.
_WRITE_TARGET_ID = {"POST": NEW_ID, "PUT": TODO_ID, "DELETE": DELETABLE_TODO_ID}


async def _apply_write_verb(client: AsyncClient, verb: str) -> Any:
    """Issue an authorized, APPLYING request for ``verb`` against ``_WRITE_TARGET_ID``.

    The token is valid and the body well-formed, so only the RESOLVED project can decide
    which tree the write lands in — which is the whole claim of the cases below.
    """
    ticket_id = _WRITE_TARGET_ID[verb]
    if verb == "POST":
        return await client.post("/api/v1/tickets", json=_draft_body(ticket_id), headers=AUTH)
    if verb == "PUT":
        return await client.put(f"/api/v1/tickets/{ticket_id}", json=_edit_body(), headers=AUTH)
    return await client.delete(f"/api/v1/tickets/{ticket_id}", headers=AUTH)


def _ticket_file(ticket_id: str) -> str:
    """The root-relative ``_snapshot`` key of ``ticket_id``'s markdown file."""
    return f"docs/planning/tickets/{ticket_id}.md"


@pytest.mark.parametrize("verb", WRITE_VERBS)
async def test_every_write_verb_refuses_with_409_when_nothing_is_selected(
    verb: str, tmp_path: Path
) -> None:
    # MONOTONICITY: a resolution that cannot establish WHICH project this is must refuse,
    # never fall back to the pinned root. The pinned tree is right there and perfectly
    # writable, so a fail-open here would answer 200/201 and mutate it — the tree the
    # snapshot proves is byte-identical afterwards.
    app, root = _real_app(tmp_path)
    app.state.selection = SelectionState(pinned_root=None, registry=None)
    before = _snapshot(root)
    async with _client(app) as client:
        resp = await _apply_write_verb(client, verb)
    assert resp.status_code == 409, verb
    assert resp.json()["error"]["code"] == "no_project_selected", verb
    assert _snapshot(root) == before


@pytest.mark.parametrize("verb", WRITE_VERBS)
async def test_every_write_verb_refuses_with_409_when_the_selected_path_is_gone(
    verb: str, tmp_path: Path
) -> None:
    # The selected row's working copy is not on this machine any more. Refusing is the
    # only safe answer: the alternative is writing a ticket into — or deleting one from —
    # whichever project happens to still be resolvable, under the vanished project's name.
    gone = tmp_path / "gone"
    gone.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(gone)
    pinned = _fixture_copy(tmp_path / "pinned")
    app = _app_over(pinned, registry=registry)
    app.state.selection.select(row.id)
    gone.rmdir()
    before = _snapshot(pinned)

    async with _client(app) as client:
        resp = await _apply_write_verb(client, verb)

    assert resp.status_code == 409, verb
    assert resp.json()["error"]["code"] == "selected_project_unavailable", verb
    # The pinned tree is exactly where a fall-back would have written. It did not.
    assert _snapshot(pinned) == before


@pytest.mark.parametrize("verb", WRITE_VERBS)
async def test_an_unauthed_write_with_no_selection_is_401_and_never_the_selection_409(
    verb: str, tmp_path: Path
) -> None:
    # Both guards would fire, and the ORDER is the contract: the write token is a
    # router-level dependency, which FastAPI solves before the handler's own
    # `get_current_project_root`. So a caller who cannot prove they may write is told
    # only that, and learns nothing about whether a project is selected or reachable.
    app, _root = _real_app(tmp_path)
    app.state.selection = SelectionState(pinned_root=None, registry=None)
    async with _client(app) as client:
        resp = await _call_write_verb(client, verb, {})
    assert resp.status_code == 401, verb
    assert resp.json()["error"]["code"] == "write_token_invalid", verb
    # Nothing about the selection may leak into the 401 envelope.
    assert "no_project_selected" not in resp.text, verb
    assert "project" not in resp.json()["error"]["message"].lower(), verb


@pytest.mark.parametrize("verb", WRITE_VERBS)
async def test_a_write_lands_in_the_selected_project_and_leaves_the_other_untouched(
    verb: str, tmp_path: Path
) -> None:
    # The fail-open regression test, and the reason this ticket is its own PR: two real
    # on-disk copies of the fixture, the FIRST one pinned at boot and the SECOND one
    # selected. Every write must land in the second tree and the first must come out
    # byte-for-byte identical. A handler that still read `app.state.project_root` would
    # pass every other case in this file and fail exactly here — by writing a ticket
    # into, or deleting one from, the wrong repository.
    first = _fixture_copy(tmp_path / "first")
    second = _fixture_copy(tmp_path / "second")
    registry = FakeProjectRegistry()
    registry.add_project(first)
    second_row = registry.add_project(second)
    app = _app_over(first, registry=registry)
    app.state.selection.select(second_row.id)
    first_before = _snapshot(first)
    second_before = _snapshot(second)

    async with _client(app) as client:
        resp = await _apply_write_verb(client, verb)
        assert resp.status_code == (201 if verb == "POST" else 200), verb
        _assert_applied_with_diff(resp.json(), _WRITE_TARGET_ID[verb])

    # The pinned project is untouched — the whole claim, over the entire tree so a stray
    # temp file or a rewritten manifest counts too.
    assert _snapshot(first) == first_before, verb

    # ...and the selected one actually changed, at the ticket file the verb names.
    second_after = _snapshot(second)
    assert second_after != second_before, verb
    target = _ticket_file(_WRITE_TARGET_ID[verb])
    if verb == "DELETE":
        assert target not in second_after
        # Still present in the pinned tree, which is what "wrong repository" would cost.
        assert target in first_before
    else:
        assert second_after[target] != second_before.get(target), verb


# --------------------------------------------------------------------------- #
# Frozen OpenAPI shape (what the frontend codegen regenerates TS types from)
# --------------------------------------------------------------------------- #


async def test_openapi_publishes_the_three_write_routes() -> None:
    async with _client(_fake_app()) as client:
        schema = (await client.get("/api/v1/openapi.json")).json()
    assert "post" in schema["paths"]["/api/v1/tickets"]
    assert "put" in schema["paths"]["/api/v1/tickets/{ticket_id}"]
    assert "delete" in schema["paths"]["/api/v1/tickets/{ticket_id}"]
    # The domain write models are the published request/response schemas — no api-model layer.
    for model in ("TicketDraft", "TicketEdit", "WriteResult", "DiffPreview"):
        assert model in schema["components"]["schemas"]


async def test_openapi_publishes_dry_run_query_and_both_create_status_codes() -> None:
    async with _client(_fake_app()) as client:
        schema = (await client.get("/api/v1/openapi.json")).json()
    create = schema["paths"]["/api/v1/tickets"]["post"]
    assert [param["name"] for param in create["parameters"]] == ["dryRun"]
    # Both outcomes are documented against the one WriteResult shape, so the SPA's
    # generated client types the dry-run response as well as the apply.
    for code in ("200", "201"):
        ref = create["responses"][code]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/WriteResult"), code


async def test_openapi_write_operations_require_the_token_scheme_and_reads_do_not() -> None:
    # require_write_token is a plain dependency FastAPI cannot infer security from, so
    # each write operation declares the published scheme itself — otherwise the document
    # would name a header no operation requires.
    async with _client(_fake_app()) as client:
        schema = (await client.get("/api/v1/openapi.json")).json()
        # FastAPI caches the document and all three operations share one declaration, so
        # the second read must be identical — never mutated or duplicated by the first.
        assert (await client.get("/api/v1/openapi.json")).json() == schema
    expected = [{WRITE_TOKEN_SCHEME_NAME: []}]
    assert schema["paths"]["/api/v1/tickets"]["post"]["security"] == expected
    assert schema["paths"]["/api/v1/tickets/{ticket_id}"]["put"]["security"] == expected
    assert schema["paths"]["/api/v1/tickets/{ticket_id}"]["delete"]["security"] == expected
    assert "security" not in schema
    assert "security" not in schema["paths"]["/api/v1/tickets"]["get"]
    assert "security" not in schema["paths"]["/api/v1/tickets/{ticket_id}"]["get"]
