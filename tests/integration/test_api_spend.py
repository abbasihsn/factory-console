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
serialised body. Since v3.0 the root is resolved through the selection seam, so the two
ways that resolution can refuse — nothing selected, and a selected path that is gone —
are pinned as 409s rather than as the "no ledger" body they most resemble.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_console.app import create_app
from factory_console.domain import Project
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.ledger import MAX_LEDGER_BYTES, MAX_SKIPPED_LINES
from factory_console.services.project_selection import SelectionState
from factory_console.store.fake_registry import FakeProjectRegistry

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


def _write_over_cap_ledger(project_root: Path) -> Path:
    """Write a ledger just past the reader's byte cap, sparsely.

    Sparse-extends past the cap rather than materialising 10 MiB of bytes — the
    same idiom ``tests/unit/test_ledger.py`` uses for the cap, since the reader
    decides on ``stat().st_size`` and never reads the content.
    """
    path = project_root / _LEDGER_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(REAL_ENTRY_LINE.encode("utf-8") + b"\n")
        handle.truncate(MAX_LEDGER_BYTES + 1)
    return path


def _make_app(project_root: Path, *, registry: FakeProjectRegistry | None = None) -> FastAPI:
    """Build the real app bound to ``project_root``, over a stub adapter.

    The spend route never touches the adapter — it reads the ledger off the root
    directly — so the fake is seeded with the bare minimum ``create_app`` needs.
    Leaving ``registry`` unset is pinned mode, which is what every ledger case here
    wants; the selection cases pass one to drive the SELECTED project instead.
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
        project_registry=registry,
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

    # The path is still reported: the no-ledger view's whole content is "there is
    # no ledger, and here is where I looked", so the probed location travels even
    # though nothing was found. ``found`` is what says it is absent.
    assert body["source"] == {
        "found": False,
        "read": False,
        "path": str(tmp_path / _LEDGER_RELATIVE),
    }
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
    assert absent["source"]["path"] == str(fresh_clone / _LEDGER_RELATIVE), (
        "the probed location travels even when nothing is there"
    )
    assert present["source"]["path"] == str(empty_path)
    assert absent != present, "a zero total and an unread ledger are not the same response"


# --------------------------------------------------------------------------- #
# A real ledger, aggregated
# --------------------------------------------------------------------------- #


def test_a_real_entry_is_aggregated_into_all_three_cuts(tmp_path: Path) -> None:
    path = _write_ledger(tmp_path, REAL_ENTRY_LINE + "\n")

    body = _get_spend(tmp_path)

    assert body["source"] == {"found": True, "read": True, "path": str(path)}
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
# The wire vocabulary is camelCase, all the way into the nested token object
# --------------------------------------------------------------------------- #


def test_token_counts_are_published_in_camel_case(tmp_path: Path) -> None:
    # ARCHITECTURE.md's REST v1 contract is "JSON camelCase". The ledger's own
    # TokenCounts is snake_case because it mirrors a file another program writes,
    # so serialising it directly would put the one snake_case object in an
    # otherwise camelCase body — and freeze it there, since /api/v1 renames are a
    # v2 change.
    _write_ledger(tmp_path, REAL_ENTRY_LINE + "\n")

    body = _get_spend(tmp_path)

    assert set(body["totals"]["tokens"]) == {
        "input",
        "output",
        "cacheRead",
        "cacheCreation",
        "total",
    }
    assert body["totals"]["tokens"]["cacheRead"] == 7261803
    assert body["totals"]["tokens"]["cacheCreation"] == 232826
    for row in body["byModel"]:
        assert set(row["tokens"]) == {
            "input",
            "output",
            "cacheRead",
            "cacheCreation",
            "total",
        }
    assert "cache_read" not in str(body), "no snake_case key survives anywhere in the body"
    assert "cache_creation" not in str(body)


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


def test_an_over_cap_ledger_is_found_but_reports_that_it_was_not_read(tmp_path: Path) -> None:
    # The gap a totals-only assertion cannot see: a ledger too big to read returns
    # ZERO entries, exactly like an empty one. Without source.read, `found: true`
    # over zeroed totals says this project was measured and cost nothing — the same
    # false statement about real money that the missing-ledger branch avoids, just
    # moved one branch later.
    _write_over_cap_ledger(tmp_path)

    body = _get_spend(tmp_path)

    assert body["source"]["found"] is True, "the file is right there"
    assert body["source"]["read"] is False, "but nothing in it was ever parsed"
    assert body["totals"]["costUsd"] == 0.0
    assert body["totals"]["entries"] == 0
    assert body["skipped"] == [{"lineNo": 0, "reason": "file_too_large"}]


def test_an_unprobeable_ledger_is_found_but_unread_rather_than_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A ledger whose directory cannot be SEARCHED is not a missing ledger, and the
    # probe behind find_ledger_path cannot say so on its own: Path.is_file() has one
    # bit for "absent" and "not allowed to look", and it does not even fail the same
    # way twice — through CPython 3.12 it re-raises EACCES, from 3.13 (gh-113978) it
    # answers False for every OSError. pyproject allows both (`>=3.11`, no upper
    # bound). Each default is a different wrong answer: the raise escapes an endpoint
    # whose docstring promises to raise nothing, and the False bills a ledger that is
    # right there as "$0.00, no ledger" — the false statement about real money the
    # whole `source` shape exists to prevent, reopened by an interpreter upgrade
    # rather than by a code change.
    #
    # Faking the EACCES at Path.stat (rather than with chmod) is the idiom
    # tests/unit/test_run_state.py uses for the same hazard, and for the same reason:
    # it pins THIS module's guard on every interpreter and under root, where a
    # chmod-000 directory is searchable and the test would pass vacuously.
    ledger = _write_ledger(tmp_path, REAL_ENTRY_LINE + "\n")
    client = TestClient(_make_app(tmp_path))  # built BEFORE the denial: app boot stats too

    real_stat = Path.stat

    def deny_the_ledger(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self == ledger:
            raise PermissionError(13, "Permission denied", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", deny_the_ledger)

    resp = client.get("/api/v1/spend")
    assert resp.status_code == 200, "an unprobeable ledger is not a server error"
    body = resp.json()

    assert body["source"]["found"] is True, "the ledger exists; we merely could not look"
    assert body["source"]["read"] is False, "so the bill is UNKNOWN, not a measured zero"
    assert body["skipped"] == [{"lineNo": 0, "reason": "unreadable"}]
    assert body["totals"]["entries"] == 0
    assert body["totals"]["costUsd"] == 0.0


def test_an_unreadable_ledger_file_is_found_but_reports_that_it_was_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The other member of `domain.spend.WHOLE_FILE_REASONS`, and the sibling of the over-cap case
    # above: the file is probeable but cannot be OPENED. Asserting only the over-cap
    # member would let a future edit narrow that frozenset and still ship green.
    # Monkeypatched rather than chmod'd for the reason given in the test above.
    #
    # Injected at ``os.open`` because that is the syscall the reader actually makes:
    # it opens ONCE and interrogates the descriptor, so there is no ``Path.stat`` or
    # ``Path.read_bytes`` in the read path to deny. Patching a call the reader no
    # longer makes would leave this test green against a ledger that read fine.
    ledger = _write_ledger(tmp_path, REAL_ENTRY_LINE + "\n")
    client = TestClient(_make_app(tmp_path))

    real_open = os.open
    denied = ledger.resolve()

    def deny_the_ledger(path: object, *args: object, **kwargs: object) -> int:
        if isinstance(path, (str, Path)) and Path(path) == denied:
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", deny_the_ledger)

    resp = client.get("/api/v1/spend")
    assert resp.status_code == 200
    body = resp.json()

    assert body["source"]["found"] is True
    assert body["source"]["read"] is False
    assert body["skipped"] == [{"lineNo": 0, "reason": "unreadable"}]
    assert body["totals"]["costUsd"] == 0.0


def test_an_unread_ledger_is_distinguishable_from_an_empty_one(tmp_path: Path) -> None:
    # The pair the bug hides between: both are `found: true` with zero dollars.
    empty = tmp_path / "empty"
    empty.mkdir()
    _write_ledger(empty, "")
    over_cap = tmp_path / "over_cap"
    over_cap.mkdir()
    _write_over_cap_ledger(over_cap)

    measured = _get_spend(empty)
    unknown = _get_spend(over_cap)

    assert measured["totals"] == unknown["totals"], "both report zero dollars"
    assert measured["source"]["read"] is True, "an empty ledger WAS read: a measured zero"
    assert unknown["source"]["read"] is False, "an over-cap ledger was not: an unknown bill"


def test_a_skipped_count_past_the_detail_cap_reaches_the_wire(tmp_path: Path) -> None:
    # Every other assertion on this field is `== 0`, which is also the value it
    # would carry if the kwarg were dropped entirely — so a passthrough typo would
    # ship undetected. MAX_SKIPPED_LINES bad lines plus two more forces it non-zero.
    bad_lines = MAX_SKIPPED_LINES + 2
    _write_ledger(tmp_path, "not json at all\n" * bad_lines)

    body = _get_spend(tmp_path)

    assert len(body["skipped"]) == MAX_SKIPPED_LINES, "the detail list stops at the cap"
    assert body["skippedOmitted"] == 2, "and the rest are still counted, not dropped"
    assert len(body["skipped"]) + body["skippedOmitted"] == bad_lines
    assert body["source"]["read"] is True, "the file WAS read; its lines just did not parse"


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
# The selection seam: no project resolved is a 409, never a "no ledger" body
# --------------------------------------------------------------------------- #


def test_spend_refuses_with_409_when_nothing_is_selected(tmp_path: Path) -> None:
    # ``found: false`` over zeroed totals is a statement ABOUT a project — "this one
    # has no ledger" — and is exactly the false-money claim this endpoint is careful
    # about, so it must not be the answer when there is no project at all.
    app = _make_app(tmp_path)
    app.state.selection = SelectionState(pinned_root=None, registry=None)

    resp = TestClient(app).get("/api/v1/spend")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_project_selected"


def test_spend_refuses_with_409_when_the_selected_path_is_gone(tmp_path: Path) -> None:
    # A registered project whose working copy is not on this machine. Refusing beats
    # falling back to the pinned root, which would bill one project for another's run.
    gone = tmp_path / "gone"
    gone.mkdir()
    registry = FakeProjectRegistry()
    row = registry.add_project(gone)
    app = _make_app(tmp_path / "pinned", registry=registry)
    app.state.selection.select(row.id)
    gone.rmdir()

    resp = TestClient(app).get("/api/v1/spend")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "selected_project_unavailable"


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
