"""Integration tests for ``GET /api/v1/runs`` and ``/api/v1/runs/{ticket_id}``.

Drive apps built with FastAPI's ``TestClient`` over the filesystem-backed
:class:`RealFileAdapter`. Every project is a THROWAWAY copy of the checked-in
``minimal`` fixture (3 tickets, no ``.factory/`` of its own) with the run
artifacts placed under it per test, so each case controls exactly which of the
four sources exist — which is the distinction this endpoint exists to draw.

The artifacts themselves are the checked-in ``tests/fixtures/runs/`` files;
``tests/fixtures/runs/README.md`` documents their provenance, and in particular
why the lane result is written from the factory's documented ``===LANE_RESULT===``
persistence contract rather than copied from a real ``.factory/results`` file (no
such file is reachable from a build worktree — ``.factory/`` is gitignored).

Pinned here: all-three-present yields an empty ``unavailable``; run-state only
NAMES ``results`` and ``receipts``; a project with no ``.factory/`` reports every
source ``found: false`` and is distinguishable from one whose sources exist and
are empty; an unsafe id is a 400 before any filesystem access; an
unknown-to-manifest id is a 404 while a manifest ticket absent from run-state is a
200; and no absolute path or ``session_id`` appears anywhere in a response.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.app import create_app
from factory_console.domain import Project, RunState, Ticket
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.real import RealFileAdapter

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MINIMAL = FIXTURES / "projects" / "minimal"
RUNS_FIXTURES = FIXTURES / "runs"
LANE_RESULT_FIXTURE = RUNS_FIXTURES / "lane-result.json"

# The ``minimal`` fixture's three manifest ids, in manifest order.
TICKET_IDS = ["TM-001", "TM-015", "TM-028"]

_FAKE_PROJECT = Project(
    rootPath=Path("/factory/demo-project"),
    ticketsManifestPath=Path("/factory/demo-project/docs/planning/tickets.json"),
    ticketsDir=Path("/factory/demo-project/docs/planning/tickets"),
    discoveredAt=datetime(2026, 7, 21, 12, 30, 0),
)


def _fake_app() -> FastAPI:
    """Build the real app over a FakeFileAdapter (used only for the invalid-id path)."""
    adapter = FakeFileAdapter(
        project=_FAKE_PROJECT,
        tickets=[
            Ticket(
                id="FAKE-1",
                title="Alpha widget",
                status="todo",
                track="backend",
                milestone="MVP",
                filePath=Path("/factory/demo-project/docs/planning/tickets/FAKE-1.md"),
                bodyMarkdown="# Alpha widget",
                bodyHtml="<h1>Alpha widget</h1>",
                raw={"id": "FAKE-1"},
            )
        ],
        run_states={"FAKE-1": RunState.ready},
    )
    return create_app(adapter, version="0.0.0", project_root=Path("/factory/demo-project"))


def _project(tmp_path: Path) -> Path:
    """Copy the read-only ``minimal`` fixture (no ``.factory/``) to a writable root."""
    root = tmp_path / "project"
    shutil.copytree(MINIMAL, root)
    return root


def _write_run_state(root: Path, tickets: dict[str, dict[str, object]]) -> None:
    """Place a factory-shaped ``.factory/run-state.json`` naming ``tickets``."""
    factory = root / ".factory"
    factory.mkdir(exist_ok=True)
    (factory / "run-state.json").write_text(
        json.dumps({"version": 1, "tickets": tickets, "parts_landed": {}}),
        encoding="utf-8",
    )


def _place_result(root: Path, ticket_id: str) -> None:
    results = root / ".factory" / "results"
    results.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LANE_RESULT_FIXTURE, results / f"{ticket_id}.json")


def _place_receipt(root: Path, ticket_id: str) -> None:
    receipts = root / ".factory" / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(RUNS_FIXTURES / "receipt.json", receipts / f"{ticket_id}.json")


def _place_last_stop(root: Path) -> None:
    (root / ".factory").mkdir(exist_ok=True)
    shutil.copy2(RUNS_FIXTURES / "last-stop.json", root / ".factory" / "last-stop.json")


def _app(root: Path) -> FastAPI:
    return create_app(RealFileAdapter(), version="0.0.0", project_root=root)


def _fully_populated(tmp_path: Path) -> Path:
    """A project with all four artifacts, ``TM-001`` carrying result + receipt."""
    root = _project(tmp_path)
    _write_run_state(
        root,
        {
            "TM-001": {"status": "merged", "pr_url": "https://example.test/pull/1"},
            "TM-015": {"status": "in_progress", "pr_url": None},
        },
    )
    _place_result(root, "TM-001")
    _place_receipt(root, "TM-001")
    _place_last_stop(root)
    return root


def _record(body: dict, ticket_id: str) -> dict:
    return next(run for run in body["runs"] if run["ticketId"] == ticket_id)


# --------------------------------------------------------------------------- #
# All sources present for a ticket -> everything populated, nothing unavailable
# --------------------------------------------------------------------------- #


def test_a_ticket_with_run_state_result_and_receipt_has_nothing_unavailable(
    tmp_path: Path,
) -> None:
    on_disk = json.loads(LANE_RESULT_FIXTURE.read_text(encoding="utf-8"))
    client = TestClient(_app(_fully_populated(tmp_path)))

    resp = client.get("/api/v1/runs/TM-001")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ticketId"] == "TM-001"
    assert body["runState"] == "merged"
    assert body["prUrl"] == "https://example.test/pull/1"
    assert body["hasReceipt"] is True
    assert body["unavailable"] == []
    assert body["result"] == {
        "status": on_disk["status"],
        "prUrl": on_disk["pr_url"],
        "route": on_disk["route"],
        "verdict": on_disk["verdict"],
        "reviewIterations": on_disk["review_iterations"],
    }


def test_the_list_reports_every_source_it_found_project_relative(tmp_path: Path) -> None:
    client = TestClient(_app(_fully_populated(tmp_path)))

    body = client.get("/api/v1/runs").json()

    assert body["sources"] == {
        "runState": {"found": True, "path": ".factory/run-state.json"},
        "results": {"found": True, "path": ".factory/results"},
        "receipts": {"found": True, "path": ".factory/receipts"},
        "lastStop": {"found": True, "path": ".factory/last-stop.json"},
    }
    assert body["lastStop"] == {"reason": "sprint cap reached with 2 lanes still flagged"}
    # One record per MANIFEST ticket, in manifest order — the response is bounded
    # by the manifest, not by whatever ids the artifacts happen to mention.
    assert [run["ticketId"] for run in body["runs"]] == TICKET_IDS


# --------------------------------------------------------------------------- #
# Run-state only -> the missing sources are NAMED, not merely nulled
# --------------------------------------------------------------------------- #


def test_run_state_only_names_results_and_receipts_in_unavailable(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _write_run_state(root, {"TM-015": {"status": "flagged", "pr_url": None}})
    client = TestClient(_app(root))

    body = client.get("/api/v1/runs/TM-015").json()

    assert body["runState"] == "flagged"
    assert body["result"] is None
    assert body["hasReceipt"] is False
    # The point of the ticket: a response that merely nulls the fields reads as
    # "the factory ran and did nothing". The sources must be named.
    assert body["unavailable"] == ["results", "receipts"]
    assert "runState" not in body["unavailable"]


def test_a_manifest_ticket_absent_from_run_state_names_run_state_too(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _write_run_state(root, {"TM-015": {"status": "flagged", "pr_url": None}})
    client = TestClient(_app(root))

    resp = client.get("/api/v1/runs/TM-028")

    # Known to the manifest, unknown to the run-state: a 200, not a 404. The state
    # is ``unknown`` (this console's "no source said") — the dedicated ``absent``
    # value belongs to T80 and does not exist in this branch yet.
    assert resp.status_code == 200
    body = resp.json()
    assert body["runState"] == "unknown"
    assert body["unavailable"] == ["runState", "results", "receipts"]


# --------------------------------------------------------------------------- #
# No .factory/ at all vs sources present but empty
# --------------------------------------------------------------------------- #


def test_a_project_with_no_factory_directory_finds_no_source(tmp_path: Path) -> None:
    client = TestClient(_app(_project(tmp_path)))

    body = client.get("/api/v1/runs").json()

    assert all(source == {"found": False, "path": None} for source in body["sources"].values())
    assert body["lastStop"] is None
    assert [run["ticketId"] for run in body["runs"]] == TICKET_IDS
    for run in body["runs"]:
        assert run["unavailable"] == ["runState", "results", "receipts"]


def test_empty_sources_are_distinguishable_from_absent_ones(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _write_run_state(root, {})
    (root / ".factory" / "results").mkdir()
    (root / ".factory" / "receipts").mkdir()
    client = TestClient(_app(root))

    body = client.get("/api/v1/runs").json()

    # The artifacts ARE there and hold nothing — which is a different fact from
    # "there is no run data here", and the endpoint has to be able to say so.
    assert body["sources"]["runState"]["found"] is True
    assert body["sources"]["results"] == {"found": True, "path": ".factory/results"}
    assert body["sources"]["receipts"] == {"found": True, "path": ".factory/receipts"}
    assert body["sources"]["lastStop"] == {"found": False, "path": None}
    for run in body["runs"]:
        assert run["result"] is None and run["hasReceipt"] is False


def test_a_present_but_unparseable_last_stop_is_still_found(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / ".factory").mkdir()
    (root / ".factory" / "last-stop.json").write_text("{not json", encoding="utf-8")
    client = TestClient(_app(root))

    body = client.get("/api/v1/runs").json()

    assert body["sources"]["lastStop"]["found"] is True
    assert body["lastStop"] == {"reason": None}


# --------------------------------------------------------------------------- #
# No absolute paths, no session ids
# --------------------------------------------------------------------------- #


def test_no_absolute_path_or_session_id_leaks_into_a_response(tmp_path: Path) -> None:
    root = _fully_populated(tmp_path)
    client = TestClient(_app(root))

    raw = client.get("/api/v1/runs").text + client.get("/api/v1/runs/TM-001").text

    assert str(root) not in raw
    assert "/tmp/" not in raw, "the fixture's absolute worktree path must not be surfaced"
    assert "session_id" not in raw and "worktree" not in raw


# --------------------------------------------------------------------------- #
# Error envelopes
# --------------------------------------------------------------------------- #


def test_an_unsafe_id_is_rejected_at_the_path_boundary_as_400() -> None:
    # Driven over the FakeFileAdapter, whose project root does not exist on disk:
    # a 400 here can only come from the Path-boundary validator, which runs before
    # the handler and therefore before any filesystem access.
    client = TestClient(_fake_app())

    # Bare ``.``/``..`` are not exercised here — an HTTP client normalises them
    # out of the URL before the server sees them. They are covered one layer down,
    # in ``tests/unit/test_runs.py``, where the adapter refuses them itself.
    for bad_id in ("bad$id", "with%20space", "T78%00", "TM-001;rm"):
        resp = client.get(f"/api/v1/runs/{bad_id}")
        assert resp.status_code == 400, bad_id
        assert resp.json()["error"]["code"] == "invalid_ticket_id", bad_id


def test_a_slashed_traversal_never_reaches_the_runs_handler() -> None:
    # A ``../``-style id carries a separator, so it cannot even match the single
    # ``{ticket_id}`` segment: the router answers 404 and no handler — hence no
    # filesystem access — runs. Kept alongside the 400 case because the two
    # rejections happen at different layers and both must hold.
    client = TestClient(_fake_app())

    resp = client.get("/api/v1/runs/..%2F..%2Fetc%2Fpasswd")

    assert resp.status_code == 404
    assert "passwd" not in resp.text


def test_an_id_unknown_to_the_manifest_is_a_404(tmp_path: Path) -> None:
    client = TestClient(_app(_fully_populated(tmp_path)))

    resp = client.get("/api/v1/runs/TM-999")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ticket_not_found"


def test_a_result_for_an_id_outside_the_manifest_contributes_no_record(tmp_path: Path) -> None:
    root = _fully_populated(tmp_path)
    _place_result(root, "TM-999")
    client = TestClient(_app(root))

    body = client.get("/api/v1/runs").json()

    assert [run["ticketId"] for run in body["runs"]] == TICKET_IDS
    assert client.get("/api/v1/runs/TM-999").status_code == 404


# --------------------------------------------------------------------------- #
# Frozen OpenAPI shape (what the frontend codegen freezes against)
# --------------------------------------------------------------------------- #


def test_openapi_publishes_the_runs_paths_and_schemas() -> None:
    client = TestClient(_fake_app())

    schema = client.get("/api/v1/openapi.json").json()

    assert "/api/v1/runs" in schema["paths"]
    assert "/api/v1/runs/{ticket_id}" in schema["paths"]
    for name in ("RunRecord", "RunListResponse", "RunSources", "SourceInfo", "RunResultSummary"):
        assert name in schema["components"]["schemas"], name
    ref = schema["paths"]["/api/v1/runs"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    assert ref.endswith("/RunListResponse")
