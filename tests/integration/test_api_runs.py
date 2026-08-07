"""Integration tests for ``GET /api/v1/runs`` — the HTTP surface over T89's service.

The endpoint owns routing, the response envelope, and — since T102 — the DISCLOSURE
boundary, and nothing else. These tests pin exactly that: what the service composed
reaches the wire unflattened but NARROWED to the declared fields, the empty project is a
full list of NAMED absences rather than a 404 or an empty list, the route is registered,
and no write verb is exposed on it.

The narrowing is asserted here per FIELD NAME; that a response schema may not publish a
free-form payload at all is the rule, and lives in
``tests/integration/test_disclosure_policy.py`` where it is generic over every route.
Both are needed: this file would pass on an endpoint that disclosed the right two keys
by luck of its fixture, and that one would pass on an endpoint that disclosed the wrong
two by declaration.

Two wirings are exercised, for two different reasons. Most cases run over
:class:`FakeRunArtifactReader` — the in-memory port implementation whose reasons are
the real reader's — because they are about the HANDLER, and seeding a read is the
shortest way to get a populated record to the wire. One case runs the REAL
:class:`RealRunArtifactReader` against real files written under ``tmp_path``, so the
whole path from a byte on disk to the JSON body is covered at least once end to end;
without it every assertion here would be about a seeded object and a fake could drift
from the reader it stands in for undetected.

Every absence assertion is made PER SOURCE, never as a count, exactly as
``tests/unit/test_run_service.py`` makes them: "one artifact is missing" is not an
answer an operator can act on, and a body that said only that would satisfy a count
assertion while losing the fact this milestone exists to show.

Since v3.0 the root is resolved through the selection seam, so three more cases run
here: the two ways resolution can refuse (nothing selected, selected path gone) are
409s rather than a list of absences, and the honest-missing rule is re-asserted over a
project reached through the REGISTRY rather than the boot-time pin.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.api.v1.runs import DISCLOSED_ARTIFACT_FIELDS, ProjectedRunRecord
from factory_console.app import create_app
from factory_console.domain import Project, Ticket
from factory_console.domain.runs import ArtifactRead
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.real import RealFileAdapter
from factory_console.file_adapter.run_artifacts import (
    FakeRunArtifactReader,
    RealRunArtifactReader,
    RunArtifactReader,
)
from factory_console.file_adapter.runs import RECEIPTS_RELATIVE_DIR, RESULTS_RELATIVE_DIR
from factory_console.services.project_selection import SelectionState
from factory_console.services.run_service import RunService
from factory_console.store.fake_registry import FakeProjectRegistry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "runs"

# The checked-in fixture project, located as ``test_api_tickets.py`` does: a real
# manifest of three tickets and no ``.factory/`` at all — a fresh clone, exactly.
MINIMAL_PROJECT = Path(__file__).resolve().parents[1] / "fixtures" / "projects" / "minimal"

# The house convention for fake-backed tests: a root that exists on no disk, so a
# handler that read the filesystem instead of the injected port could not pass.
FAKE_ROOT = Path("/factory/demo-project")


def _project(root: Path) -> Project:
    return Project(
        rootPath=root,
        ticketsManifestPath=root / "docs/planning/tickets.json",
        ticketsDir=root / "docs/planning/tickets",
        roadmapPath=root / "ROADMAP.md",
        runStateDir=root / ".factory/run-state",
        discoveredAt=datetime(2026, 8, 3, 12, 0, 0),
    )


def _ticket(ticket_id: str) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=f"Ticket {ticket_id}",
        status="todo",
        track="backend",
        milestone="v2.1",
        filePath=Path(f"/factory/demo-project/docs/planning/tickets/{ticket_id}.md"),
        bodyMarkdown=f"# {ticket_id}",
        bodyHtml=f"<h1>{ticket_id}</h1>",
        raw={"id": ticket_id},
    )


def _app(
    ticket_ids: list[str],
    artifacts: RunArtifactReader,
    *,
    root: Path = FAKE_ROOT,
    registry: FakeProjectRegistry | None = None,
) -> FastAPI:
    """Build the real app over a seeded manifest and the given artifact reader.

    Leaving ``registry`` unset is pinned mode — the selection can never leave
    ``root`` — which is what every artifact case here wants; the selection cases
    pass one so the resolved project is the SELECTED one instead.
    """
    project = _project(root)
    adapter = FakeFileAdapter(project=project, tickets=[_ticket(i) for i in ticket_ids])
    return create_app(
        adapter,
        version="0.0.0",
        project_root=root,
        run_artifact_reader=artifacts,
        project_registry=registry,
    )


def _get_runs(
    ticket_ids: list[str],
    artifacts: RunArtifactReader,
    *,
    root: Path = FAKE_ROOT,
) -> dict:
    """GET ``/api/v1/runs`` for a seeded app, asserting a 200."""
    client = TestClient(_app(ticket_ids, artifacts, root=root))
    resp = client.get("/api/v1/runs")
    assert resp.status_code == 200
    return resp.json()


def _write_artifact(root: Path, relative: Path, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# A populated project: what the service composed is what the wire carries
# --------------------------------------------------------------------------- #


def test_a_seeded_project_returns_one_record_per_ticket_with_both_artifacts() -> None:
    result = ArtifactRead(
        path=FAKE_ROOT / RESULTS_RELATIVE_DIR / "T89.json", data={"status": "ready"}
    )
    receipt = ArtifactRead(
        path=FAKE_ROOT / RECEIPTS_RELATIVE_DIR / "T89.json", data={"verdict": "pass"}
    )
    artifacts = FakeRunArtifactReader(results={"T89": result}, receipts={"T89": receipt})

    body = _get_runs(["T89"], artifacts)

    assert set(body) == {"items", "total"}
    assert body["total"] == 1
    (record,) = body["items"]
    assert record["ticketId"] == "T89"
    # Both sources travel as their own three-field object — path, data and the (null)
    # reason — because the failure this record type exists to prevent is a flattening:
    # a ``hasResult`` boolean or a bare null would satisfy any assertion that only
    # checked ``data``. What travels INSIDE ``data`` is the declared projection, so the
    # objects are asserted exactly.
    assert record["result"] == {
        "path": str(FAKE_ROOT / RESULTS_RELATIVE_DIR / "T89.json"),
        "data": {"status": "ready"},
        "reason": None,
    }
    # ``verdict`` is not disclosed, so the receipt READ fine and discloses nothing:
    # ``{}``, which is not ``None``. The two must stay distinguishable — an empty
    # object says "read, and it names none of the keys this console asks for", a null
    # says "not read", and the reason beside it is what names why.
    assert record["receipt"] == {
        "path": str(FAKE_ROOT / RECEIPTS_RELATIVE_DIR / "T89.json"),
        "data": {},
        "reason": None,
    }


def test_the_body_is_the_services_output_record_for_record_once_projected() -> None:
    """The handler adds nothing but the projection: the body is the service's list, narrowed.

    The endpoint's whole contract. Asserted against the service run over the SAME two
    ports rather than against a hand-written expectation, so a handler that filtered,
    re-ordered, or re-derived any part of the listing fails here even if the shape it
    produced is individually plausible. The one transformation it IS allowed is the
    disclosure projection, so the expectation is the service's own records put through
    :meth:`ProjectedRunRecord.from_record` — the same function the handler calls, which
    makes this a test of the LISTING (order, count, per-source pairing) and leaves what
    the projection discloses to the cases that name the fields.
    """
    result = ArtifactRead(path=FAKE_ROOT / RESULTS_RELATIVE_DIR / "T88.json", data={"status": "ok"})
    artifacts = FakeRunArtifactReader(results={"T88": result})
    ticket_ids = ["T88", "T89", "T90"]

    project = _project(FAKE_ROOT)
    adapter = FakeFileAdapter(project=project, tickets=[_ticket(i) for i in ticket_ids])
    expected = [
        ProjectedRunRecord.from_record(record)
        for record in RunService(adapter, artifacts).list_run_records(project)
    ]

    body = _get_runs(ticket_ids, artifacts)

    assert body["items"] == [json.loads(record.model_dump_json()) for record in expected]
    assert body["total"] == len(expected)


def test_one_present_source_beside_one_absent_source_is_reported_independently() -> None:
    # A lane that wrote a result and no receipt. The two sources are independent, so
    # neither reason may be inferred from the other — over HTTP as in the service.
    result = ArtifactRead(
        path=FAKE_ROOT / RESULTS_RELATIVE_DIR / "T89.json", data={"status": "ready"}
    )
    body = _get_runs(["T89"], FakeRunArtifactReader(results={"T89": result}))

    (record,) = body["items"]
    assert record["result"]["reason"] is None
    assert record["result"]["data"] == {"status": "ready"}
    assert record["receipt"]["reason"] == "absent"
    assert record["receipt"]["data"] is None


def test_records_arrive_in_manifest_order() -> None:
    body = _get_runs(["T90", "T88", "T89"], FakeRunArtifactReader())

    assert [record["ticketId"] for record in body["items"]] == ["T90", "T88", "T89"], (
        "the manifest is the list, and its order is the response's order"
    )


# --------------------------------------------------------------------------- #
# A project with NO artifacts: 200 with named absences, not a 404 and not []
# --------------------------------------------------------------------------- #


def test_a_project_with_no_artifacts_names_every_absent_source(tmp_path: Path) -> None:
    # The case this ticket exists to get right, and it runs over the REAL reader
    # against a real empty tree: .factory/ is gitignored, so a fresh clone has no
    # artifacts at all, and that is an answer the console must render rather than an
    # error or a silence.
    ticket_ids = ["T88", "T89", "T90"]

    client = TestClient(_app(ticket_ids, RealRunArtifactReader(), root=tmp_path))
    resp = client.get("/api/v1/runs")

    assert resp.status_code == 200, "no artifacts is not a 404"
    body = resp.json()
    assert body["total"] == 3
    assert [record["ticketId"] for record in body["items"]] == ticket_ids, (
        "one record per MANIFEST ticket even when the factory never ran — an empty "
        "list would report the console's silence as the manifest's"
    )
    for record in body["items"]:
        # Per source, deliberately: a count of missing artifacts would pass here and
        # tell an operator nothing about WHICH source is missing.
        assert record["result"]["reason"] == "absent"
        assert record["result"]["data"] is None
        assert record["receipt"]["reason"] == "absent"
        assert record["receipt"]["data"] is None
        # ``absent`` still names the file it is about — the only thing an operator can
        # act on — so the path travels on the empty outcome too.
        assert record["result"]["path"].endswith(f"{record['ticketId']}.json")
        assert record["receipt"]["path"].endswith(f"{record['ticketId']}.json")


def test_a_real_fixture_project_with_no_artifacts_is_200_over_the_real_adapter() -> None:
    """The ticket's headline case, over BOTH real collaborators rather than a fake one.

    Every other case in this file seeds a :class:`FakeFileAdapter`, whose
    ``load_project`` ignores the root it is handed and returns its seeded project —
    so ``ProjectNotFound`` (the only route to a 404 on this endpoint) is structurally
    unreachable in them, and their "not a 404" assertions cannot fail for the reason
    they name. This one runs the pairing the two production entry points actually
    wire, :class:`RealFileAdapter` + :class:`RealRunArtifactReader`, over the
    checked-in ``minimal`` fixture: a real manifest with three tickets and no
    ``.factory/`` directory at all. That is the fresh clone the ticket is about, and
    it must answer 200 with three fully-named absences.
    """
    app = create_app(
        RealFileAdapter(),
        version="0.0.0",
        project_root=MINIMAL_PROJECT,
        run_artifact_reader=RealRunArtifactReader(),
    )
    resp = TestClient(app).get("/api/v1/runs")

    assert resp.status_code == 200, "a real project with no .factory/ is not a 404"
    body = resp.json()
    assert [record["ticketId"] for record in body["items"]] == ["TM-001", "TM-015", "TM-028"], (
        "one record per MANIFEST ticket, in the real manifest's order"
    )
    assert body["total"] == 3
    for record in body["items"]:
        assert record["result"]["reason"] == "absent"
        assert record["result"]["data"] is None
        assert record["receipt"]["reason"] == "absent"
        assert record["receipt"]["data"] is None


def test_a_selected_project_with_no_factory_dir_names_every_absent_source() -> None:
    """The honest-missing rule survives the v3.0 resolution change, on the SELECTED path.

    The two cases above reach ``minimal``/``tmp_path`` through the boot-time PIN. This
    one reaches it through the registry — a project the operator added and selected,
    resolved per request by ``get_current_project_root`` — with the pin pointing
    somewhere else entirely, so a handler that still read ``app.state.project_root``
    could not pass. It is the case a multi-project console hits constantly, because
    ``.factory/`` is gitignored and most registered projects will have none: it must
    still be 200 with three fully-named absences, not ``[]`` and not a 404.
    """
    registry = FakeProjectRegistry()
    row = registry.add_project(MINIMAL_PROJECT)
    app = create_app(
        RealFileAdapter(),
        version="0.0.0",
        project_root=FAKE_ROOT,
        run_artifact_reader=RealRunArtifactReader(),
        project_registry=registry,
    )
    app.state.selection.select(row.id)

    resp = TestClient(app).get("/api/v1/runs")

    assert resp.status_code == 200, "a selected project with no .factory/ is not a 404"
    body = resp.json()
    assert [record["ticketId"] for record in body["items"]] == ["TM-001", "TM-015", "TM-028"], (
        "the SELECTED project's manifest, not the pinned root's — and not an empty list"
    )
    assert body["total"] == 3
    for record in body["items"]:
        assert record["result"]["reason"] == "absent"
        assert record["result"]["data"] is None
        assert record["receipt"]["reason"] == "absent"
        assert record["receipt"]["data"] is None


# --------------------------------------------------------------------------- #
# The selection seam: no project resolved is a 409, never a list of absences
# --------------------------------------------------------------------------- #


def test_runs_refuses_with_409_when_nothing_is_selected() -> None:
    # A list of named absences is a statement ABOUT a project — "this one has never
    # run" — so it must not be the answer when there is no project to make it about.
    app = _app(["T88"], FakeRunArtifactReader())
    app.state.selection = SelectionState(pinned_root=None, registry=None)

    resp = TestClient(app).get("/api/v1/runs")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_project_selected"


def test_runs_refuses_with_409_when_the_selected_path_is_gone(tmp_path: Path) -> None:
    # "Registered but the working copy is not on this machine" is NOT the same as
    # "no artifacts": resolution refuses instead of falling back to the pinned root
    # and reporting another project's run-state under this project's name.
    gone = tmp_path / "gone"
    gone.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(gone)
    app = _app(["T88"], FakeRunArtifactReader(), root=tmp_path / "pinned", registry=registry)
    app.state.selection.select(row.id)
    gone.rmdir()

    resp = TestClient(app).get("/api/v1/runs")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "selected_project_unavailable"


def test_an_empty_manifest_is_the_only_way_to_an_empty_list() -> None:
    # The converse of the case above, stated so the two cannot be confused: an empty
    # body means the MANIFEST is empty, never that the artifacts are.
    body = _get_runs([], FakeRunArtifactReader())

    assert body == {"items": [], "total": 0}


# --------------------------------------------------------------------------- #
# End to end over the real reader: bytes on disk reach the body
# --------------------------------------------------------------------------- #


def test_real_artifacts_on_disk_reach_the_response_body(tmp_path: Path) -> None:
    result_text = (FIXTURES / "result.json").read_text(encoding="utf-8")
    receipt_text = (FIXTURES / "receipt.json").read_text(encoding="utf-8")
    _write_artifact(tmp_path, RESULTS_RELATIVE_DIR / "T89.json", result_text)
    _write_artifact(tmp_path, RECEIPTS_RELATIVE_DIR / "T89.json", receipt_text)

    body = _get_runs(["T88", "T89"], RealRunArtifactReader(), root=tmp_path)

    by_id = {record["ticketId"]: record for record in body["items"]}
    # The bytes reached the endpoint — and the endpoint disclosed the declared fields
    # out of them, not the file. The fixture carries six more keys (``branch``,
    # ``started_at``, ``review_rounds``, …); none of them is on the wire.
    source = json.loads(result_text)
    assert by_id["T89"]["result"]["data"] == {
        "status": source["status"],
        "pr_url": source["pr_url"],
    }
    # The receipt fixture names none of the declared fields, so it discloses nothing —
    # and still reports itself as READ (``{}`` with a null reason), which is the fact
    # the frontend needs to tell it from a receipt that is missing.
    assert by_id["T89"]["receipt"]["data"] == {}
    assert by_id["T89"]["result"]["reason"] is None
    assert by_id["T89"]["receipt"]["reason"] is None
    # The never-run neighbour keeps its own named absence rather than inheriting
    # anything from the populated one.
    assert by_id["T88"]["result"]["reason"] == "absent"
    assert by_id["T88"]["receipt"]["reason"] == "absent"


def test_a_malformed_artifact_is_unparseable_over_http_not_a_failed_request(
    tmp_path: Path,
) -> None:
    # The port is TOTAL, so one corrupt file cannot fail the listing: it arrives as a
    # named reason beside its healthy neighbours.
    _write_artifact(tmp_path, RESULTS_RELATIVE_DIR / "T89.json", "{not json at all")

    body = _get_runs(["T89"], RealRunArtifactReader(), root=tmp_path)

    (record,) = body["items"]
    assert record["result"]["reason"] == "unparseable", "a file that is there is not 'absent'"
    assert record["receipt"]["reason"] == "absent", "and the missing source keeps its own reason"


# --------------------------------------------------------------------------- #
# The disclosure boundary: only the declared fields leave the process
# --------------------------------------------------------------------------- #


def test_an_undeclared_key_beside_the_declared_ones_never_reaches_the_wire() -> None:
    """The rule, over HTTP: extra keys stay in the file the console cannot model.

    ``session_id`` and ``raw_log`` stand for what nobody in this repo can rule out of a
    lane's result — the factory's own metrics carry session ids, model names and cost,
    and ``tests/fixtures/runs/README.md`` says plainly that this console does not know
    what else these artifacts contain. That unknowability is the ARGUMENT for the
    projection, not a reason to forward it: the declared fields come through and
    everything else stops here.

    Asserted over the whole serialised body, not just the ``data`` object, so a key that
    reappeared anywhere — hoisted onto the record, echoed in an error detail — still
    fails.
    """
    result = ArtifactRead(
        path=FAKE_ROOT / RESULTS_RELATIVE_DIR / "T89.json",
        data={
            "status": "ready",
            "pr_url": "https://example.test/pr/1",
            "session_id": "sess-deadbeef",
            "raw_log": "line one\nline two",
        },
    )
    body = _get_runs(["T89"], FakeRunArtifactReader(results={"T89": result}))

    (record,) = body["items"]
    assert record["result"]["data"] == {
        "status": "ready",
        "pr_url": "https://example.test/pr/1",
    }
    serialised = json.dumps(body)
    assert "session_id" not in serialised
    assert "sess-deadbeef" not in serialised
    assert "raw_log" not in serialised


def test_a_declared_key_holding_a_non_string_is_omitted_rather_than_coerced() -> None:
    # "Read a field or do not, never guess" — the same rule the view's ``readString``
    # applies. ``str(value)`` on an object this console cannot model would publish a
    # rendering of a payload it does not understand, which is the disclosure the
    # projection exists to refuse. The neighbouring declared key is unaffected: the
    # projection is per field, not per artifact.
    result = ArtifactRead(
        path=FAKE_ROOT / RESULTS_RELATIVE_DIR / "T89.json",
        data={"status": "ready", "pr_url": {"href": "https://example.test/pr/1"}},
    )
    body = _get_runs(["T89"], FakeRunArtifactReader(results={"T89": result}))

    (record,) = body["items"]
    assert record["result"]["data"] == {"status": "ready"}
    assert "example.test" not in json.dumps(body)


def test_the_disclosed_fields_are_the_ones_the_frontend_declares() -> None:
    """The server's allowlist and the view's ``PROJECTED_FIELDS`` are the SAME two names.

    Read out of the frontend source rather than transcribed, because a transcription is
    a third copy that drifts silently. Two declarations, one set of names: this is what
    keeps them from becoming two independent guesses (``ARCHITECTURE.md``, "Other
    factory artefacts (read-only)").
    """
    source = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "routes"
        / "runs"
        / "+page.svelte"
    ).read_text(encoding="utf-8")
    declaration = re.search(r"export const PROJECTED_FIELDS = \[(.*?)\] as const;", source)

    assert declaration is not None, "PROJECTED_FIELDS is no longer declared as this test reads it"
    frontend_fields = tuple(re.findall(r"'([^']+)'", declaration.group(1)))
    assert frontend_fields == DISCLOSED_ARTIFACT_FIELDS


# --------------------------------------------------------------------------- #
# The route is registered, and it is read-only
# --------------------------------------------------------------------------- #


def test_openapi_publishes_the_runs_path_and_its_response_schema() -> None:
    client = TestClient(_app(["T89"], FakeRunArtifactReader()))
    resp = client.get("/api/v1/openapi.json")

    assert resp.status_code == 200
    schema = resp.json()
    assert "/api/v1/runs" in schema["paths"]
    operations = schema["paths"]["/api/v1/runs"]
    assert set(operations) == {"get"}, "the published contract exposes no write verb"
    ref = operations["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/RunListResponse")
    assert "RunListResponse" in schema["components"]["schemas"]


@pytest.mark.parametrize("verb", ["post", "put", "patch", "delete"])
def test_no_write_verb_is_exposed_on_the_runs_path(verb: str) -> None:
    client = TestClient(_app(["T89"], FakeRunArtifactReader()))

    resp = getattr(client, verb)("/api/v1/runs")

    assert resp.status_code == 405, "this endpoint is read-only"


def test_a_single_run_path_is_not_registered() -> None:
    # There is no ``GET /runs/{id}`` in this ticket, and an unknown /api/v1 path must
    # keep its 404 rather than fall through to the SPA shell.
    client = TestClient(_app(["T89"], FakeRunArtifactReader()))

    assert client.get("/api/v1/runs/T89").status_code == 404


# --------------------------------------------------------------------------- #
# The DI seam: an unwired reader is a wiring bug, not a silent empty answer
# --------------------------------------------------------------------------- #


def test_an_app_built_without_an_artifact_reader_raises_rather_than_answering() -> None:
    """No reader bound → ``RuntimeError``, not a body claiming the factory ran nothing.

    Degrading here would report every artifact as unread — a claim about the FACTORY
    made from a fact about the console's own wiring, which is exactly the collapse
    ``ArtifactSkipReason`` forbids. The other v1 routes are unaffected: the seam is
    per-handler, so only ``/runs`` needs the reader.
    """
    project = _project(FAKE_ROOT)
    adapter = FakeFileAdapter(project=project, tickets=[_ticket("T89")])
    app = create_app(adapter, version="0.0.0", project_root=FAKE_ROOT)
    client = TestClient(app)

    with pytest.raises(RuntimeError, match="run_artifact_reader"):
        client.get("/api/v1/runs")

    assert client.get("/api/v1/tickets").status_code == 200, "only /runs needs the reader"
