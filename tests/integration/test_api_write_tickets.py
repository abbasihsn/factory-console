"""Boundary-contract tests for the ticket write endpoints (T73).

Companion to ``test_api_tickets_write.py`` (T65) over the same three routes. The
near-identical names split like this, and the two suites never restate each other:

* **``test_api_tickets_write.py`` owns the ROUTE SEMANTICS** — which verb answers which
  status, what the ``WriteResult`` body carries, the ``?dryRun`` query guard, and the
  frozen OpenAPI shape.
* **this file owns the BOUNDARY CONTRACT those semantics rest on** — that every guard
  fires BEFORE the ``FileWriter`` port is touched (counted AT the port), that every
  write failure renders the ONE REST v1 error envelope, and that an applied write
  really lands on disk rather than in the serving process's memory.

Why counting port calls is a different proof from T65's ``_snapshot`` byte-compare: an
unchanged tree is ALSO what a write that ran and happened to be a no-op — or one that
wrote and rolled back — leaves behind. Only :class:`_RecordingFileWriter` can say the
guard fired before the writer was asked to do anything, which is the actual security
property of the token, id, and query guards.

Deterministic and I/O-free except for the durability section, which writes only under
``tmp_path``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from _write_support import (
    AUTH,
    PINNED_TOKEN,
    WRONG_TOKEN,
    app_over,
)
from _write_support import (
    client as _client,
)
from _write_support import (
    real_app as _real_app,
)
from fastapi import FastAPI
from httpx import AsyncClient, Response

from factory_console.app import create_app
from factory_console.config import WRITE_TOKEN_HEADER
from factory_console.domain import Project, RunState
from factory_console.domain.write import DiffPreview, TicketDraft, TicketEdit, WriteResult
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.fake_writer import FakeFileWriter
from factory_console.file_adapter.manifest import manifest_entry_to_ticket_stub
from factory_console.file_adapter.writer_protocol import FileWriter

# Ids as the checked-in fixture stages them, reused verbatim for the in-memory pair so
# one id means the same thing in both wirings: CAD-131 is ``todo`` (mutable), CAD-118 is
# ``ready`` (the todo-only gate refuses it), CAD-999 exists nowhere.
TODO_ID = "CAD-131"
READY_ID = "CAD-118"
MISSING_ID = "CAD-999"
NEW_ID = "CAD-300"

# Outside TICKET_ID_PATTERN, so the request is rejected during validation.
INVALID_ID = "bad$id"

# The write port's two halves. ``preview_*`` is pure by contract; only these three can
# change anything, so "no write occurred" is a claim about THIS set.
_APPLY_METHODS = frozenset({"create_ticket", "edit_ticket", "delete_ticket"})

# Which pure preview a dry-run of each verb is allowed to reach, and nothing else.
_PREVIEW_FOR_VERB = {
    "POST": "preview_create",
    "PUT": "preview_edit",
    "DELETE": "preview_delete",
}


def _draft_body(ticket_id: str = NEW_ID, **overrides: Any) -> dict[str, Any]:
    """A valid ``TicketDraft`` request body (``extra='forbid'``, so no stray keys)."""
    body: dict[str, Any] = {
        "id": ticket_id,
        "title": "Retention cohort report",
        "track": "analytics",
        "milestone": "v2",
        "dependsOn": [],
        "provides": "Weekly cohort retention across every tracked habit",
        "files": ["server/cadence/analytics/cohorts.py"],
        "bodyMarkdown": "# Retention cohorts\n\nCohort report body.\n",
    }
    body.update(overrides)
    return body


def _edit_body(**overrides: Any) -> dict[str, Any]:
    """A valid ``TicketEdit`` request body (``TicketDraft`` minus ``id``)."""
    body: dict[str, Any] = {
        "title": "Weekly digest email (boundary edit)",
        "track": "notifications",
        "milestone": "v1",
        "dependsOn": [],
        "provides": "Monday-morning digest",
        "files": ["server/cadence/notifications/weekly_digest.py"],
        "bodyMarkdown": "# Weekly digest\n\nBoundary-edited body.\n",
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
# The recording write port — the instrument the zero-write proofs read
# --------------------------------------------------------------------------- #


class _RecordingFileWriter:
    """A ``FileWriter`` that logs every port call and delegates to ``inner``.

    The shipped :class:`FakeFileWriter` computes a faithful in-memory write but keeps no
    call log, and production code must not grow one just to be observed — so the log
    lives here, as a thin decorator over the port the app already depends on.

    Two logs rather than one, because the guards under test fail at two different
    depths. ``entered`` records a call on the way IN, so a guard that fires before the
    port leaves it empty. ``returned`` records only after the inner writer returned, so
    a name in ``entered`` but not ``returned`` is a call that RAISED — it produced no
    :class:`WriteResult` and committed nothing. The todo-only gate needs exactly that
    distinction: it lives INSIDE the writer's apply methods, so the port genuinely is
    called, and "no write occurred" there means the apply aborted rather than never
    started.
    """

    def __init__(self, inner: FileWriter) -> None:
        self._inner = inner
        self.entered: list[str] = []
        self.returned: list[str] = []

    def _call(self, name: str, *args: Any) -> Any:
        """Delegate ``name`` to the inner writer, logging entry and successful return."""
        self.entered.append(name)
        result = getattr(self._inner, name)(*args)
        self.returned.append(name)
        return result

    def preview_create(self, project: Project, draft: TicketDraft) -> DiffPreview:
        """Record and delegate the pure create preview."""
        return self._call("preview_create", project, draft)

    def create_ticket(self, project: Project, draft: TicketDraft) -> WriteResult:
        """Record and delegate the create apply."""
        return self._call("create_ticket", project, draft)

    def preview_edit(self, project: Project, ticket_id: str, edit: TicketEdit) -> DiffPreview:
        """Record and delegate the pure edit preview."""
        return self._call("preview_edit", project, ticket_id, edit)

    def edit_ticket(self, project: Project, ticket_id: str, edit: TicketEdit) -> WriteResult:
        """Record and delegate the edit apply."""
        return self._call("edit_ticket", project, ticket_id, edit)

    def preview_delete(self, project: Project, ticket_id: str) -> DiffPreview:
        """Record and delegate the pure delete preview."""
        return self._call("preview_delete", project, ticket_id)

    def delete_ticket(self, project: Project, ticket_id: str) -> WriteResult:
        """Record and delegate the delete apply."""
        return self._call("delete_ticket", project, ticket_id)

    @property
    def applies_entered(self) -> list[str]:
        """The mutating calls that were STARTED, in order."""
        return [name for name in self.entered if name in _APPLY_METHODS]

    @property
    def applies_returned(self) -> list[str]:
        """The mutating calls that COMPLETED — a started call missing here raised."""
        return [name for name in self.returned if name in _APPLY_METHODS]


# --------------------------------------------------------------------------- #
# App wirings
# --------------------------------------------------------------------------- #


def _entry(ticket_id: str, *, status: str = "todo") -> dict[str, Any]:
    """One manifest entry, the shape both in-memory ports are seeded from."""
    return {
        "id": ticket_id,
        "title": f"Ticket {ticket_id}",
        "status": status,
        "track": "backend",
        "milestone": "v1",
        "dependsOn": [],
        "provides": f"provides {ticket_id}",
        "files": [],
    }


def _spied_app() -> tuple[FastAPI, _RecordingFileWriter]:
    """Build the app over the in-memory pair with the write port wrapped in the spy.

    Both ports are seeded from the SAME manifest entries and the SAME run-state map, so
    the adapter's existence check and the writer's mutability gate agree about which
    tickets exist and in what state — otherwise an edit would 404 in ``WriteService``
    before the port question could even be asked. ``roadmapPath`` is left unset so a
    write plans the two coupled files and no third, keeping the recorded call sequence
    the only moving part.

    No filesystem is involved: the paths below never have to exist.
    """
    root = Path("/factory/spy-project")
    project = Project(
        rootPath=root,
        ticketsManifestPath=root / "docs" / "planning" / "tickets.json",
        ticketsDir=root / "docs" / "planning" / "tickets",
        runStateDir=root / ".factory" / "run-state",
        discoveredAt=datetime(2026, 7, 26, 12, 0, 0),
    )
    entries = [_entry(TODO_ID), _entry(READY_ID, status="in_review")]
    bodies = {entry["id"]: f"# {entry['title']}\n\nSeeded body.\n" for entry in entries}
    run_states = {TODO_ID: RunState.todo, READY_ID: RunState.ready}
    tickets = [
        manifest_entry_to_ticket_stub(entry, project.ticketsDir).model_copy(
            update={"bodyMarkdown": bodies[entry["id"]]}
        )
        for entry in entries
    ]

    writer = _RecordingFileWriter(
        FakeFileWriter(manifest=entries, bodies=bodies, run_states=run_states)
    )
    app = create_app(
        FakeFileAdapter(project=project, tickets=tickets, run_states=run_states),
        version="0.0.0",
        project_root=project.rootPath,
        file_writer=writer,
        write_token=PINNED_TOKEN,
    )
    return app, writer


async def _write(
    client: AsyncClient,
    verb: str,
    *,
    headers: dict[str, str],
    draft_id: str = NEW_ID,
    path_id: str = TODO_ID,
    params: dict[str, str] | None = None,
) -> Response:
    """Issue a WELL-FORMED request for ``verb`` so only the guard under test can decide.

    A create carries its id in the BODY and an edit/delete in the PATH, so the two ids
    are separate parameters: a test that wants one bad id across all three verbs sets
    both, and a test that only cares about the verb leaves both at their valid defaults.
    """
    if verb == "POST":
        return await client.post(
            "/api/v1/tickets", json=_draft_body(ticket_id=draft_id), headers=headers, params=params
        )
    if verb == "PUT":
        return await client.put(
            f"/api/v1/tickets/{path_id}", json=_edit_body(), headers=headers, params=params
        )
    return await client.delete(f"/api/v1/tickets/{path_id}", headers=headers, params=params)


# --------------------------------------------------------------------------- #
# The guards fire BEFORE the write port — proved at the port, not on disk
# --------------------------------------------------------------------------- #


def test_the_recording_writer_satisfies_the_file_writer_port() -> None:
    # Every "no port call happened" assertion below is only as good as the instrument
    # taking it, and each one is satisfied by an EMPTY log — so a spy that quietly stopped
    # covering some of the port would make those tests pass for the wrong reason. Nothing
    # in the DI seam type-checks the writer, so the port is enforced here instead:
    # ``FileWriter`` is ``@runtime_checkable``, which holds the decorator to the same
    # structural bar as ``RealFileWriter`` and ``FakeFileWriter``, and a method added to
    # the port later fails HERE rather than silently going unrecorded.
    _app, writer = _spied_app()
    assert isinstance(writer, FileWriter)


@pytest.mark.parametrize("verb", ["POST", "PUT", "DELETE"])
@pytest.mark.parametrize(
    ("case", "headers"),
    [("missing", {}), ("wrong", {WRITE_TOKEN_HEADER: WRONG_TOKEN})],
    ids=["missing-token", "wrong-token"],
)
async def test_a_rejected_write_token_never_reaches_the_write_port(
    verb: str, case: str, headers: dict[str, str]
) -> None:
    # The security property behind the 401 is not the status code — it is that an
    # unauthorized caller cannot make the server so much as PLAN a write. The token
    # dependency sits on the router, ahead of the handler that resolves the writer, so
    # the port log must be completely empty for every verb and both failing shapes.
    app, writer = _spied_app()
    async with _client(app) as client:
        resp = await _write(client, verb, headers=headers)
    assert resp.status_code == 401, f"{verb}/{case}"
    assert resp.json()["error"]["code"] == "write_token_invalid", f"{verb}/{case}"
    assert writer.entered == [], f"{verb}/{case}"


@pytest.mark.parametrize("verb", ["POST", "PUT", "DELETE"])
async def test_an_invalid_ticket_id_never_reaches_the_write_port(verb: str) -> None:
    # A valid token, so the rejection is the id pattern and not the token. The id is
    # refused during request validation — at the ``Path`` param for edit/delete, at the
    # ``TicketDraft.id`` body field for create — which is upstream of the handler, so no
    # unsafe id is ever handed to a writer that would turn it into a filesystem path.
    app, writer = _spied_app()
    async with _client(app) as client:
        resp = await _write(client, verb, headers=AUTH, draft_id=INVALID_ID, path_id=INVALID_ID)
    assert resp.status_code == 400, verb
    assert resp.json()["error"]["code"] == "invalid_ticket_id", verb
    assert writer.entered == [], verb


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"dryrun": "true"}, "unknown_query_param"),
        ({"dryRun": ["true", "false"]}, "repeated_query_param"),
    ],
    ids=["misspelled-flag", "repeated-flag"],
)
async def test_an_unusable_dry_run_flag_never_reaches_the_write_port(
    params: dict[str, Any], code: str
) -> None:
    # T65 pins that these query shapes are refused and leave the tree byte-identical.
    # The stronger claim is that the refusal happens before the port: a caller who asked
    # for a preview and mistyped the flag must not have a delete PLANNED on their behalf,
    # let alone applied. DELETE is the verb whose apply removes a file, so it is the one
    # worth pinning.
    app, writer = _spied_app()
    async with _client(app) as client:
        resp = await client.delete(f"/api/v1/tickets/{TODO_ID}", headers=AUTH, params=params)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == code
    assert writer.entered == []


@pytest.mark.parametrize("verb", ["POST", "PUT", "DELETE"])
async def test_a_dry_run_reaches_only_the_pure_preview_half_of_the_write_port(verb: str) -> None:
    # The dry-run promise is not "the bytes happened to come out the same" — it is that
    # the mutating half of the port is never invoked. Pinned as an EXACT call log rather
    # than "no applies": a preview that also called a sibling preview, or called the same
    # one twice, would be doing work the contract does not describe.
    app, writer = _spied_app()
    async with _client(app) as client:
        resp = await _write(client, verb, headers=AUTH, params={"dryRun": "true"})
    assert resp.status_code == 200, verb
    assert resp.json()["applied"] is False, verb
    assert writer.entered == [_PREVIEW_FOR_VERB[verb]], verb
    assert writer.applies_entered == [], verb


@pytest.mark.parametrize(
    ("verb", "apply_method"),
    [("PUT", "edit_ticket"), ("DELETE", "delete_ticket")],
)
async def test_a_non_todo_apply_aborts_inside_the_writer_and_commits_nothing(
    verb: str, apply_method: str
) -> None:
    # The one guard that legitimately REACHES the port: unlike the token, id, and query
    # guards, the todo-only gate lives inside the writer's apply methods (``write_gate``
    # is the writer's first step), so asserting "zero port calls" here would be asserting
    # something false. The true contract is two-part and pinned as such:
    #
    #   * the apply was ENTERED but never RETURNED — it raised, so it produced no
    #     WriteResult and committed nothing, and
    #   * the port's state is unchanged — the diff a preview computes AFTER the refused
    #     apply is identical to the one it computed BEFORE it. That is what fails if the
    #     gate is ever reordered to run after the manifest/body mutation, which the
    #     status code alone would not notice.
    app, writer = _spied_app()
    async with _client(app) as client:
        before = (
            await _write(client, "PUT", headers=AUTH, path_id=READY_ID, params={"dryRun": "true"})
        ).json()["diff"]

        resp = await _write(client, verb, headers=AUTH, path_id=READY_ID)

        after = (
            await _write(client, "PUT", headers=AUTH, path_id=READY_ID, params={"dryRun": "true"})
        ).json()["diff"]

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ticket_not_mutable"
    assert writer.applies_entered == [apply_method]
    assert writer.applies_returned == []
    assert after == before


# --------------------------------------------------------------------------- #
# One error envelope for every write failure (the REST v1 contract)
# --------------------------------------------------------------------------- #

# Every failure mode the three write routes can produce, each as the request that
# triggers it. The sweep below holds them ALL to one structural contract; T65 pins the
# per-case behaviour, so what is new here is that the SHAPE never varies — the SPA has
# a single error branch and must not need a per-endpoint special case.
_WRITE_FAILURES = [
    pytest.param(
        "POST",
        "/api/v1/tickets",
        {"json": _draft_body()},
        401,
        "write_token_invalid",
        id="401-missing-token",
    ),
    pytest.param(
        "DELETE",
        f"/api/v1/tickets/{TODO_ID}",
        {"headers": {WRITE_TOKEN_HEADER: WRONG_TOKEN}},
        401,
        "write_token_invalid",
        id="401-wrong-token",
    ),
    pytest.param(
        "PUT",
        f"/api/v1/tickets/{INVALID_ID}",
        {"headers": AUTH, "json": _edit_body()},
        400,
        "invalid_ticket_id",
        id="400-invalid-ticket-id",
    ),
    pytest.param(
        "DELETE",
        f"/api/v1/tickets/{TODO_ID}?dryrun=true",
        {"headers": AUTH},
        400,
        "unknown_query_param",
        id="400-unknown-query-param",
    ),
    pytest.param(
        "DELETE",
        f"/api/v1/tickets/{TODO_ID}?dryRun=true&dryRun=false",
        {"headers": AUTH},
        400,
        "repeated_query_param",
        id="400-repeated-query-param",
    ),
    pytest.param(
        "PUT",
        f"/api/v1/tickets/{MISSING_ID}",
        {"headers": AUTH, "json": _edit_body()},
        404,
        "ticket_not_found",
        id="404-ticket-not-found",
    ),
    pytest.param(
        "DELETE",
        f"/api/v1/tickets/{READY_ID}",
        {"headers": AUTH},
        409,
        "ticket_not_mutable",
        id="409-ticket-not-mutable",
    ),
    pytest.param(
        "POST",
        "/api/v1/tickets",
        {"headers": AUTH, "json": _draft_body(ticket_id=TODO_ID)},
        409,
        "write_conflict",
        id="409-write-conflict",
    ),
    pytest.param(
        "POST",
        "/api/v1/tickets",
        # A body missing a required field: the 422 arm of the validation handler, the one
        # failure whose ``details`` is a LIST (pydantic's error entries) rather than a dict.
        {"headers": AUTH, "json": {k: v for k, v in _draft_body().items() if k != "title"}},
        422,
        "validation_error",
        id="422-validation-error",
    ),
]


@pytest.mark.parametrize(("method", "url", "request_kwargs", "status", "code"), _WRITE_FAILURES)
async def test_every_write_failure_renders_the_same_error_envelope(
    method: str,
    url: str,
    request_kwargs: dict[str, Any],
    status: int,
    code: str,
    tmp_path: Path,
) -> None:
    # ``{error: {code, message, details?}}`` is the whole of the REST v1 error contract
    # (ARCHITECTURE.md -> Contracts -> REST v1), and the SPA renders every failure through
    # one component. Anything that leaked a second top-level key, dropped ``message``, or
    # emitted a partial ``WriteResult`` alongside the error would break that component for
    # one endpoint only — which is exactly the kind of drift a per-case ``error.code``
    # assertion cannot see. The expected ``code`` is asserted too, so a case that silently
    # stopped triggering its failure mode (and answered some OTHER uniform envelope) still
    # fails here.
    app, _root = _real_app(tmp_path)
    async with _client(app) as client:
        resp = await client.request(method, url, **request_kwargs)

    assert resp.status_code == status
    assert resp.headers["content-type"].startswith("application/json")

    body = resp.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert {"code", "message"} <= set(error) <= {"code", "message", "details"}
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"].strip()
    # ``details`` is optional structured context, never a bare scalar or an explicit
    # null: ``to_error_response`` drops the key entirely when there is nothing to say.
    if "details" in error:
        assert isinstance(error["details"], dict | list)

    # Part of the same envelope contract: the write token is the console's only secret,
    # and most of these bodies echo some of the request back (``details`` carries the
    # offending query keys, the ticket id, or pydantic's ``input``). Checked on EVERY
    # failure mode rather than the 401 alone, because the leak that matters is the one
    # nobody thought to look for: a handler that started folding request headers into
    # ``details`` would sail straight past a 401-only check.
    assert PINNED_TOKEN not in resp.text
    assert WRONG_TOKEN not in resp.text


# --------------------------------------------------------------------------- #
# Realism: an applied write is on DISK, not in the serving process
# --------------------------------------------------------------------------- #


def _manifest_ids(root: Path) -> list[str]:
    """The ticket ids the on-disk manifest lists, read as bytes rather than via a port."""
    manifest = json.loads((root / "docs" / "planning" / "tickets.json").read_text("utf-8"))
    return [entry["id"] for entry in manifest["tickets"]]


async def test_an_applied_create_lands_on_disk_and_a_fresh_app_reads_it_back(
    tmp_path: Path,
) -> None:
    # T65 proves a create is observable through a GET on the SAME app. That app holds the
    # writer that just ran, so on its own the GET cannot distinguish a durable write from
    # a value cached in process state. Two independent checks close the gap: the files
    # themselves are read straight off disk (bypassing both ports), and a SECOND app —
    # its own ``RealFileAdapter`` instance, sharing nothing with the first — serves the
    # ticket. That is the realism claim the fake pair structurally cannot make.
    app, root = _real_app(tmp_path)
    async with _client(app) as client:
        resp = await client.post("/api/v1/tickets", json=_draft_body(), headers=AUTH)
    assert resp.status_code == 201

    md_path = root / "docs" / "planning" / "tickets" / f"{NEW_ID}.md"
    assert "Cohort report body." in md_path.read_text("utf-8")
    assert NEW_ID in _manifest_ids(root)

    async with _client(app_over(root)) as fresh:
        detail = await fresh.get(f"/api/v1/tickets/{NEW_ID}")
    assert detail.status_code == 200
    assert detail.json()["title"] == "Retention cohort report"


async def test_an_applied_delete_removes_the_files_and_a_fresh_app_no_longer_serves_it(
    tmp_path: Path,
) -> None:
    # The mirror claim, and the one a stale cache would hide in the opposite direction: a
    # delete that only forgot the ticket in memory would still leave the ``.md`` and the
    # manifest entry behind for the next process to resurrect.
    app, root = _real_app(tmp_path)
    md_path = root / "docs" / "planning" / "tickets" / f"{TODO_ID}.md"
    assert md_path.exists()

    async with _client(app) as client:
        resp = await client.delete(f"/api/v1/tickets/{TODO_ID}", headers=AUTH)
    assert resp.status_code == 200

    assert not md_path.exists()
    assert TODO_ID not in _manifest_ids(root)

    async with _client(app_over(root)) as fresh:
        detail = await fresh.get(f"/api/v1/tickets/{TODO_ID}")
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "ticket_not_found"


# --------------------------------------------------------------------------- #
# The dry-run flag is published uniformly across all three verbs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path", "verb"),
    [
        ("/api/v1/tickets", "post"),
        ("/api/v1/tickets/{ticket_id}", "put"),
        ("/api/v1/tickets/{ticket_id}", "delete"),
    ],
)
async def test_openapi_publishes_the_dry_run_flag_on_every_write_verb(path: str, verb: str) -> None:
    # ``?dryRun`` is shared by all three verbs at runtime, but the SPA only gets a typed
    # way to send it on the operations that DECLARE it — and the generated client is what
    # the diff-preview modal calls. T65 pins the declaration on create; sweeping all three
    # is what stops edit or delete from losing the flag while create keeps it.
    app, _writer = _spied_app()
    async with _client(app) as client:
        schema = (await client.get("/api/v1/openapi.json")).json()
    params = {param["name"]: param for param in schema["paths"][path][verb]["parameters"]}
    assert params["dryRun"]["in"] == "query"
    assert params["dryRun"]["schema"]["type"] == "boolean"
