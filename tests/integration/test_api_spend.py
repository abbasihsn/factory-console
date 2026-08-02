"""Integration tests for ``GET /api/v1/spend`` over a real project root on disk.

Unlike its sibling endpoints, this one reads the ledger through T79's plain
``find_ledger_path``/``read_ledger`` functions rather than through the
``FileAdapter`` protocol, so these tests point ``create_app`` at a REAL ``tmp_path``
root and write a real ``.factory/metrics/ledger.jsonl`` under it. The adapter is
still injected (``create_app`` requires one) but this route never calls it.

The fixture line is the same verbatim real ledger line ``tests/unit/test_ledger.py``
and ``tests/unit/test_spend_calc.py`` use — three models in one ``by_model`` object,
and a session id that must not survive the trip to HTTP.

Pins the three things the ticket says a passing bug would slip through: no ledger
and an EMPTY ledger are distinguishable responses (both report zero dollars, so a
test that checks only the totals passes on the bug), skipped lines are visible so a
partial total is visibly partial, and no ``session_id`` appears anywhere in the
serialised body.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.app import create_app
from factory_console.domain import Project
from factory_console.file_adapter import FakeFileAdapter

# The same verbatim real ledger line the unit tests read (see the module docstring).
REAL_ENTRY_LINE = (
    '{"ts":"2026-07-30T16:33:22Z","agent":"lead","level":"ticket","ids":["T71"],'
    '"model":"sonnet","effort":"medium","wall_min":12,"turns":27,"peak_context":133027,'
    '"tokens":{"input":8546,"output":40143,"cache_read":7261803,'
    '"cache_creation":232826,"total":7543318},'
    '"cost_usd":5.740558350000003,"cost_scope":"lane",'
    '"session_id":"81dda660-3f1a-4c67-9f0e-2b7c5d9a1e04",'
    '"review_tier":null,"sessions":1,'
    '"by_model":{'
    '"claude-haiku-4-5-20251001":{"input":112,"output":903,"cache_read":0,'
    '"cache_creation":0,"cost_usd":0.0041205},'
    '"claude-sonnet-5":{"input":8434,"output":39240,"cache_read":7261803,'
    '"cache_creation":232826,"cost_usd":5.02143785},'
    '"claude-opus-4-8[1m]":{"input":0,"output":0,"cache_read":0,'
    '"cache_creation":0,"cost_usd":0.7150000}}}'
)

SESSION_ID = "81dda660-3f1a-4c67-9f0e-2b7c5d9a1e04"

_LEDGER_RELATIVE = Path(".factory") / "metrics" / "ledger.jsonl"


def _write_ledger(project_root: Path, text: str) -> Path:
    """Write ``text`` as the project's ledger and return its path."""
    path = project_root / _LEDGER_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _make_app(project_root: Path) -> FastAPI:
    """Build the real app bound to ``project_root``, over a stub adapter.

    The spend route never touches the adapter — it reads the ledger off the root
    directly — so the fake is seeded with the bare minimum ``create_app`` needs.
    """
    project = Project(
        rootPath=project_root,
        ticketsManifestPath=project_root / "docs/planning/tickets.json",
        ticketsDir=project_root / "docs/planning/tickets",
        discoveredAt=datetime(2026, 7, 21, 12, 30, 0),
    )
    return create_app(
        FakeFileAdapter(project=project, tickets=[]),
        version="0.0.0",
        project_root=project_root,
    )


def _get_spend(project_root: Path) -> dict:
    """GET ``/api/v1/spend`` for a project root, asserting a 200."""
    client = TestClient(_make_app(project_root))
    resp = client.get("/api/v1/spend")
    assert resp.status_code == 200
    return resp.json()


# --------------------------------------------------------------------------- #
# THE absence rule: no ledger is not a zero bill
# --------------------------------------------------------------------------- #


def test_a_project_with_no_ledger_reports_source_not_found(tmp_path: Path) -> None:
    # A fresh clone: .factory/ is gitignored, so having no ledger is NORMAL, and
    # rendering it as "$0.00" would be a false statement about real money.
    body = _get_spend(tmp_path)

    assert body["source"] == {"found": False, "path": None}
    assert body["totals"]["costUsd"] == 0.0
    assert body["totals"]["entries"] == 0
    assert body["byTicket"] == []
    assert body["byModel"] == []
    assert body["byLevel"] == []
    assert body["skipped"] == []
    assert body["attribution"] == "full-to-each-id"


def test_no_ledger_and_an_empty_ledger_are_distinguishable_responses(tmp_path: Path) -> None:
    # The bug this test exists for: both report zero dollars, so a test asserting
    # only on the totals passes even when the endpoint has collapsed an UNKNOWN
    # bill into a MEASURED zero.
    fresh_clone = tmp_path / "fresh_clone"
    fresh_clone.mkdir()
    empty_ledger = tmp_path / "empty_ledger"
    empty_ledger.mkdir()
    empty_path = _write_ledger(empty_ledger, "")

    absent = _get_spend(fresh_clone)
    present = _get_spend(empty_ledger)

    assert absent["totals"] == present["totals"], "both are honestly zero dollars"
    assert absent["source"]["found"] is False
    assert present["source"]["found"] is True, "an empty ledger WAS read"
    assert absent["source"]["path"] is None
    assert present["source"]["path"] == str(empty_path)
    assert absent != present, "a zero total and an unread ledger are not the same response"


# --------------------------------------------------------------------------- #
# A real ledger, aggregated
# --------------------------------------------------------------------------- #


def test_a_real_entry_is_aggregated_into_all_three_cuts(tmp_path: Path) -> None:
    path = _write_ledger(tmp_path, REAL_ENTRY_LINE + "\n")

    body = _get_spend(tmp_path)

    assert body["source"] == {"found": True, "path": str(path)}
    assert body["totals"]["costUsd"] == 5.74055835, "rounded once, at the boundary"
    assert body["totals"]["entries"] == 1
    assert body["totals"]["tokens"]["total"] == 7543318
    assert body["byTicket"] == [
        {
            "ticketId": "T71",
            "attributedCostUsd": 5.74055835,
            "entries": 1,
            "models": [
                "claude-haiku-4-5-20251001",
                "claude-opus-4-8[1m]",
                "claude-sonnet-5",
            ],
        }
    ]
    by_model = {row["model"]: row["costUsd"] for row in body["byModel"]}
    assert by_model == {
        "claude-haiku-4-5-20251001": 0.0041205,
        "claude-sonnet-5": 5.02143785,
        "claude-opus-4-8[1m]": 0.715,
    }, "model ids verbatim, at the factory's exact figures"
    assert body["byLevel"] == [{"level": "ticket", "costUsd": 5.74055835, "entries": 1}]
    assert body["skipped"] == []


# --------------------------------------------------------------------------- #
# A partial total is visibly partial
# --------------------------------------------------------------------------- #


def test_skipped_lines_appear_so_a_partial_total_says_so(tmp_path: Path) -> None:
    valid_json_but_not_a_spend_record = '{"agent":"lead"}'
    _write_ledger(
        tmp_path,
        f"{REAL_ENTRY_LINE}\nthis is not json at all\n{valid_json_but_not_a_spend_record}\n",
    )

    body = _get_spend(tmp_path)

    assert body["totals"]["entries"] == 1, "the good line still counts"
    assert body["skipped"] == [
        {"lineNo": 2, "reason": "not_json"},
        {"lineNo": 3, "reason": "invalid_entry"},
    ], "a total over 1 of 3 lines must carry which lines it could not read"
    assert body["skippedOmitted"] == 0


def test_the_skipped_excerpt_is_not_exposed_over_http(tmp_path: Path) -> None:
    # T79 keeps a truncated excerpt for a human reading the file; the wire shape
    # deliberately carries only the line number and the reason.
    _write_ledger(tmp_path, "a-recognisable-malformed-line\n")

    body = _get_spend(tmp_path)

    (skip,) = body["skipped"]
    assert set(skip) == {"lineNo", "reason"}
    assert "a-recognisable-malformed-line" not in str(body)


def test_a_directory_at_the_ledger_path_is_no_ledger_at_all(tmp_path: Path) -> None:
    # A directory at the ledger path is not a file, so it is not FOUND at all —
    # the reader's node-type check, surfaced end to end.
    (tmp_path / _LEDGER_RELATIVE).mkdir(parents=True)

    body = _get_spend(tmp_path)

    assert body["source"]["found"] is False


# --------------------------------------------------------------------------- #
# The session id never reaches the wire
# --------------------------------------------------------------------------- #


def test_the_body_never_carries_a_lane_agent_marker(tmp_path: Path) -> None:
    # Asserted over the WHOLE body, not per field: the failure mode is a field
    # nobody thought to check, in a shape added later. The test's own name avoids
    # the literal it searches for — pytest builds ``tmp_path`` from it, and the
    # response legitimately carries that path.
    _write_ledger(tmp_path, f"{REAL_ENTRY_LINE}\n{REAL_ENTRY_LINE}\n")

    client = TestClient(_make_app(tmp_path))
    resp = client.get("/api/v1/spend")

    assert resp.status_code == 200
    assert "session_id" not in resp.text
    assert "sessionId" not in resp.text
    assert SESSION_ID not in resp.text
    assert "81dda660" not in resp.text


def test_a_malformed_line_leading_with_a_session_id_does_not_leak_it(tmp_path: Path) -> None:
    # The line does not parse, so it becomes a skip — and the skip shape carries
    # no excerpt, which is where a session id could otherwise ride out.
    _write_ledger(tmp_path, f'{{"session_id":"{SESSION_ID}","ts":"nope",oops\n')

    client = TestClient(_make_app(tmp_path))
    resp = client.get("/api/v1/spend")

    assert resp.status_code == 200
    assert SESSION_ID not in resp.text
    assert resp.json()["skipped"] == [{"lineNo": 1, "reason": "not_json"}]


# --------------------------------------------------------------------------- #
# The published contract
# --------------------------------------------------------------------------- #


def test_openapi_publishes_the_spend_path_and_its_response_schema(tmp_path: Path) -> None:
    client = TestClient(_make_app(tmp_path))
    resp = client.get("/api/v1/openapi.json")

    assert resp.status_code == 200
    schema = resp.json()
    assert "/api/v1/spend" in schema["paths"]
    ref = schema["paths"]["/api/v1/spend"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    assert ref.endswith("/SpendResponse")
    assert "SpendResponse" in schema["components"]["schemas"]
