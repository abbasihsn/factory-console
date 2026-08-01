"""Unit tests for :mod:`factory_console.file_adapter.runs` — the run artifacts.

Cover the three artifacts that sit beside ``run-state.json``: the lane result
(field by field, against the checked-in ``tests/fixtures/runs/lane-result.json``),
receipt PRESENCE, and ``last-stop.json``'s deliberate degrade-don't-fail
behaviour. Also pin the two invariants the endpoint's NFRs rest on: an unsafe
ticket id is refused BEFORE any filesystem access, and the module is read-only.

On the fixture's provenance: ``tests/fixtures/runs/README.md`` states it in full.
The short version is that ``.factory/results`` is gitignored and no real lane
result was reachable from the sandboxed worktree this was built in, so the
fixture is written from the factory's own documented ``===LANE_RESULT===``
persistence contract for that file — contract-grounded, not arbitrary — and it
deliberately carries the keys the console does NOT model so a parser that
requires a narrower shape fails here.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
from _read_only_guard import (
    assert_module_carries_read_only_header,
    assert_module_is_read_only,
)

from factory_console.domain import RunStateSource
from factory_console.file_adapter import real_runs as real_runs_module
from factory_console.file_adapter import runs as runs_module
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.run_state import probe_ticket_state
from factory_console.file_adapter.runs import (
    find_last_stop_file,
    find_receipts_dir,
    find_results_dir,
    find_run_state_path,
    has_receipt,
    read_last_stop,
    read_pr_urls,
    read_result,
)

RUNS_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "runs"
LANE_RESULT_FIXTURE = RUNS_FIXTURES / "lane-result.json"
LAST_STOP_FIXTURE = RUNS_FIXTURES / "last-stop.json"
RECEIPT_FIXTURE = RUNS_FIXTURES / "receipt.json"
RUN_STATE_FIXTURE = RUNS_FIXTURES.parent / "run_state" / "run-state.json"


def _place_result(root: Path, ticket_id: str, source: Path = LANE_RESULT_FIXTURE) -> Path:
    """Copy ``source`` to ``<root>/.factory/results/<ticket_id>.json``."""
    results = root / ".factory" / "results"
    results.mkdir(parents=True, exist_ok=True)
    target = results / f"{ticket_id}.json"
    shutil.copy2(source, target)
    return target


def _place_receipt(root: Path, ticket_id: str) -> Path:
    receipts = root / ".factory" / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    target = receipts / f"{ticket_id}.json"
    shutil.copy2(RECEIPT_FIXTURE, target)
    return target


# --------------------------------------------------------------------------- #
# read_result — every modelled field, against the contract-grounded fixture
# --------------------------------------------------------------------------- #


def test_every_modelled_result_field_reads_back_from_the_fixture(tmp_path: Path) -> None:
    on_disk = json.loads(LANE_RESULT_FIXTURE.read_text(encoding="utf-8"))
    _place_result(tmp_path, "T78")

    result = read_result(tmp_path, "T78")

    assert result is not None
    # One assertion per modelled field, each against the value the FILE carries —
    # so a field that silently stops parsing (a renamed alias, a dropped type)
    # fails here rather than reporting null and reading as "the factory did
    # nothing".
    assert result.status == on_disk["status"] == "ready"
    assert result.prUrl == on_disk["pr_url"]
    assert result.route == on_disk["route"] == "deep"
    assert result.verdict == on_disk["verdict"] == "clean"
    assert result.reviewIterations == on_disk["review_iterations"] == 2


def test_a_result_naming_none_of_the_modelled_fields_is_reported_absent(tmp_path: Path) -> None:
    # The schema is taken from the factory's ``===LANE_RESULT===`` contract and is
    # NOT verified against a real file, so RENAMED keys are the likeliest form of
    # disagreement — and they never raise ``ValidationError`` (only a wrong TYPE
    # on a modelled key does). ``extra="ignore"`` plus five optional fields would
    # otherwise validate such a file into an all-null summary that the caller
    # reports as an ANSWERED source: every field null with nothing named in
    # ``unavailable``, which is precisely what this endpoint exists to prevent.
    results = tmp_path / ".factory" / "results"
    results.mkdir(parents=True)
    (results / "T78.json").write_text(
        json.dumps({"state": "ready", "pull_request": "https://example.test/pull/1"}),
        encoding="utf-8",
    )

    assert read_result(tmp_path, "T78") is None


def test_a_result_naming_one_modelled_field_still_answers(tmp_path: Path) -> None:
    # The guard above must catch "none of the fields", not "not all of them": a
    # partial result is a real answer, and reporting it absent would lose data.
    results = tmp_path / ".factory" / "results"
    results.mkdir(parents=True)
    (results / "T78.json").write_text(json.dumps({"status": "ready"}), encoding="utf-8")

    result = read_result(tmp_path, "T78")

    assert result is not None
    assert result.status == "ready"
    assert result.verdict is None


@pytest.mark.parametrize(
    "hostile_url",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>x</script>",
        "/pull/1",
        # ``urlsplit`` RAISES ValueError("Invalid IPv6 URL") on an unbalanced
        # ``[`` in the authority. Unparseable is the clearest kind of bad url,
        # so it must DROP like the rest rather than propagate out of validation.
        "https://exa[mple.test/pull/1",
        "https://[::1/pull/1",
    ],
)
def test_a_pr_url_with_a_non_http_scheme_is_dropped(hostile_url: str, tmp_path: Path) -> None:
    # ``pr_url`` is arbitrary text out of a file another process writes, and its
    # only purpose is to become an ``href``. Without the scheme allowlist a
    # corrupted or hostile artifact turns "can write under .factory/" into script
    # execution in the page holding the write token.
    results = tmp_path / ".factory" / "results"
    results.mkdir(parents=True)
    (results / "T78.json").write_text(
        json.dumps({"status": "ready", "pr_url": hostile_url}), encoding="utf-8"
    )

    result = read_result(tmp_path, "T78")

    assert result is not None
    assert result.status == "ready", "the rest of the result must survive a dropped url"
    assert result.prUrl is None


def test_result_ignores_the_fields_the_console_does_not_model(tmp_path: Path) -> None:
    # The factory owns this schema and may extend it: unmodelled keys must be
    # ignored, never rejected. ``worktree`` in particular is an ABSOLUTE host path
    # that must not become part of any response.
    on_disk = json.loads(LANE_RESULT_FIXTURE.read_text(encoding="utf-8"))
    assert on_disk["worktree"].startswith("/"), "fixture must carry an absolute worktree path"
    _place_result(tmp_path, "T78")

    result = read_result(tmp_path, "T78")

    assert result is not None
    dumped = result.model_dump()
    assert set(dumped) == {"status", "prUrl", "route", "verdict", "reviewIterations"}
    assert "worktree" not in json.dumps(dumped)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("{not json at all", id="unparseable"),
        pytest.param(json.dumps(["T78"]), id="document-not-an-object"),
        pytest.param("", id="empty-file"),
        pytest.param("[" * 20_000 + "]" * 20_000, id="too-deeply-nested"),
        # A modelled key present with the wrong type: the factory owns this
        # schema, and a type change there must degrade, not 500 the endpoint.
        pytest.param(json.dumps({"review_iterations": "two"}), id="modelled-key-wrong-type"),
    ],
)
def test_a_malformed_result_is_absent_rather_than_an_error(payload: str, tmp_path: Path) -> None:
    results = tmp_path / ".factory" / "results"
    results.mkdir(parents=True)
    (results / "T78.json").write_text(payload, encoding="utf-8")

    assert read_result(tmp_path, "T78") is None


def test_result_is_none_when_the_directory_or_the_file_is_absent(tmp_path: Path) -> None:
    assert find_results_dir(tmp_path) is None
    assert read_result(tmp_path, "T78") is None

    _place_result(tmp_path, "T78")
    assert find_results_dir(tmp_path) == tmp_path / ".factory" / "results"
    assert read_result(tmp_path, "T99") is None, "another ticket's result is not this ticket's"


def test_a_result_missing_a_modelled_key_keeps_the_keys_it_has(tmp_path: Path) -> None:
    results = tmp_path / ".factory" / "results"
    results.mkdir(parents=True)
    (results / "T78.json").write_text(json.dumps({"status": "flagged"}), encoding="utf-8")

    result = read_result(tmp_path, "T78")

    assert result is not None
    assert result.status == "flagged"
    assert result.prUrl is None and result.verdict is None


# --------------------------------------------------------------------------- #
# has_receipt — presence only
# --------------------------------------------------------------------------- #


def test_receipt_presence_is_a_boolean_and_content_is_never_parsed(tmp_path: Path) -> None:
    assert find_receipts_dir(tmp_path) is None
    assert has_receipt(tmp_path, "T78") is False

    _place_receipt(tmp_path, "T78")
    assert has_receipt(tmp_path, "T78") is True
    assert has_receipt(tmp_path, "T99") is False

    # A receipt whose content is garbage is still a receipt: nothing reads it.
    (tmp_path / ".factory" / "receipts" / "T99.json").write_text("{not json", encoding="utf-8")
    assert has_receipt(tmp_path, "T99") is True


def test_a_receipt_directory_is_not_a_receipt_file(tmp_path: Path) -> None:
    (tmp_path / ".factory" / "receipts" / "T78.json").mkdir(parents=True)

    assert has_receipt(tmp_path, "T78") is False


# --------------------------------------------------------------------------- #
# read_last_stop — absent is None; present-but-opaque is an empty LastStop
# --------------------------------------------------------------------------- #


def test_last_stop_reads_reason_and_ignores_the_rest(tmp_path: Path) -> None:
    on_disk = json.loads(LAST_STOP_FIXTURE.read_text(encoding="utf-8"))
    (tmp_path / ".factory").mkdir()
    shutil.copy2(LAST_STOP_FIXTURE, tmp_path / ".factory" / "last-stop.json")

    last_stop = read_last_stop(tmp_path)

    assert last_stop is not None
    assert last_stop.reason == on_disk["reason"]
    assert set(last_stop.model_dump()) == {"reason"}, (
        "only 'reason' is modelled; the fixture's extra keys must be ignored"
    )


def test_absent_last_stop_is_none(tmp_path: Path) -> None:
    assert read_last_stop(tmp_path) is None


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("{not json at all", id="unparseable"),
        pytest.param(json.dumps(["stopped"]), id="document-not-an-object"),
        pytest.param(json.dumps({"why": "cap"}), id="no-reason-key"),
        pytest.param(json.dumps({"reason": 17}), id="reason-not-a-string"),
    ],
)
def test_a_present_but_unusable_last_stop_stays_present(payload: str, tmp_path: Path) -> None:
    # Presence is a fact the endpoint reports separately (sources.lastStop.found),
    # so a file that is there but says nothing this console understands must not
    # collapse into "there is no file" — and must not fail the request either.
    (tmp_path / ".factory").mkdir()
    (tmp_path / ".factory" / "last-stop.json").write_text(payload, encoding="utf-8")

    last_stop = read_last_stop(tmp_path)

    assert last_stop is not None
    assert last_stop.reason is None


# --------------------------------------------------------------------------- #
# read_pr_urls — one parser for run-state.json, shared with T78
# --------------------------------------------------------------------------- #


def test_pr_urls_come_from_the_committed_factory_run_state_fixture() -> None:
    source = RunStateSource(kind="json", path=RUN_STATE_FIXTURE)

    urls = read_pr_urls(source, RUN_STATE_FIXTURE.parent)

    assert urls["T01"] == "https://github.com/abbasihsn/factory-console/pull/1"
    assert urls["T74"] == "https://github.com/abbasihsn/factory-console/pull/173"
    # The factory writes ``null`` for a ticket with no PR yet: absent, not None.
    assert "T03" not in urls and "T77" not in urls


def test_pr_urls_are_empty_without_a_json_source(tmp_path: Path) -> None:
    assert read_pr_urls(None, tmp_path) == {}
    assert read_pr_urls(RunStateSource(kind="directory", path=tmp_path), tmp_path) == {}


def test_a_symlinked_run_state_is_not_read_for_pr_urls(tmp_path: Path) -> None:
    # The fourth artifact gets the same containment guard as the other three:
    # an out-of-root run-state.json is neither parsed nor reported as found,
    # rather than being read while the endpoint renders its in-root-looking path.
    root = tmp_path / "project"
    (root / ".factory").mkdir(parents=True)
    outside = tmp_path / "outside-run-state.json"
    outside.write_text(
        json.dumps({"tickets": {"T01": {"status": "merged", "pr_url": "https://evil.test/1"}}}),
        encoding="utf-8",
    )
    link = root / ".factory" / "run-state.json"
    link.symlink_to(outside)
    source = RunStateSource(kind="json", path=link)

    assert read_pr_urls(source, root) == {}
    assert find_run_state_path(source, root) is None


# --------------------------------------------------------------------------- #
# Path safety — refused BEFORE any filesystem access
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_id",
    ["../etc/passwd", "..", ".", "a/b", "T78\n", "", "with space", "T78$"],
)
@pytest.mark.parametrize("call", [read_result, has_receipt], ids=["read_result", "has_receipt"])
def test_an_unsafe_ticket_id_is_refused(
    bad_id: str, call: Callable[[Path, str], object], tmp_path: Path
) -> None:
    # Populate the artifacts first, so a raised PathTraversal proves the id was
    # rejected rather than the directories merely being missing.
    _place_result(tmp_path, "T78")
    _place_receipt(tmp_path, "T78")

    with pytest.raises(PathTraversal) as excinfo:
        call(tmp_path, bad_id)

    assert excinfo.value.code == "invalid_ticket_id"
    assert excinfo.value.status == 400


def test_an_unsafe_id_is_refused_even_with_no_factory_directory(tmp_path: Path) -> None:
    # Validation happens BEFORE the directory probe, so the refusal does not
    # depend on the artifacts existing — a traversal is never merely "not found".
    with pytest.raises(PathTraversal):
        read_result(tmp_path, "../../etc/passwd")
    with pytest.raises(PathTraversal):
        has_receipt(tmp_path, "../../etc/passwd")


def test_path_traversal_uses_the_uniform_invalid_ticket_id_contract() -> None:
    from factory_console.file_adapter.run_state import PathTraversal as RunStatePathTraversal

    assert PathTraversal is RunStatePathTraversal


def test_the_run_state_read_path_refuses_a_bare_dot_segment_identically(tmp_path: Path) -> None:
    # ``runs`` and ``run_state`` share ONE segment rule, so they cannot drift on
    # what they reject or on the envelope they reject it with. (``ticket_md`` and
    # ``write_render`` still check the pattern only — see path_safety's docstring;
    # aligning them changes write-endpoint status codes and is not T81's call.)
    run_state_dir = tmp_path / ".factory" / "run-state"
    run_state_dir.mkdir(parents=True)
    refusers: list[Callable[[str], object]] = [
        lambda tid: read_result(tmp_path, tid),
        lambda tid: has_receipt(tmp_path, tid),
        lambda tid: probe_ticket_state(run_state_dir, tid),
    ]
    for bad in ("..", ".", "../etc/passwd", "T01\n"):
        for refuse in refusers:
            with pytest.raises(PathTraversal) as excinfo:
                refuse(bad)
            assert excinfo.value.status == 400
            assert excinfo.value.code == "invalid_ticket_id"


# --------------------------------------------------------------------------- #
# Containment — a validated id bounds the JOIN, not what the join RESOLVES to
# --------------------------------------------------------------------------- #


def test_a_symlinked_results_directory_is_not_read(tmp_path: Path) -> None:
    # The id is perfectly safe; the DIRECTORY is the escape. Without a resolved
    # containment check, ``is_dir()`` follows the link and the out-of-root lane
    # result is surfaced in a response that still reports an in-root path.
    outside = tmp_path / "outside" / "results"
    outside.mkdir(parents=True)
    shutil.copy2(LANE_RESULT_FIXTURE, outside / "TM-001.json")

    root = tmp_path / "project"
    (root / ".factory").mkdir(parents=True)
    (root / ".factory" / "results").symlink_to(outside, target_is_directory=True)

    assert find_results_dir(root) is None
    assert read_result(root, "TM-001") is None


def test_a_symlinked_result_file_inside_an_in_root_directory_is_not_read(tmp_path: Path) -> None:
    # Narrower escape: the results directory is genuinely in-root, only the one
    # entry points out. The per-ticket join needs its own containment check.
    outside = tmp_path / "outside"
    outside.mkdir()
    escapee = outside / "TM-001.json"
    shutil.copy2(LANE_RESULT_FIXTURE, escapee)

    root = tmp_path / "project"
    results = root / ".factory" / "results"
    results.mkdir(parents=True)
    (results / "TM-001.json").symlink_to(escapee)

    assert find_results_dir(root) == results
    assert read_result(root, "TM-001") is None


def test_a_symlinked_receipt_is_not_counted_as_present(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    escapee = outside / "TM-001.json"
    shutil.copy2(RECEIPT_FIXTURE, escapee)

    root = tmp_path / "project"
    receipts = root / ".factory" / "receipts"
    receipts.mkdir(parents=True)
    (receipts / "TM-001.json").symlink_to(escapee)

    assert has_receipt(root, "TM-001") is False


def test_a_symlinked_last_stop_is_not_read(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    escapee = outside / "last-stop.json"
    shutil.copy2(LAST_STOP_FIXTURE, escapee)

    root = tmp_path / "project"
    (root / ".factory").mkdir(parents=True)
    (root / ".factory" / "last-stop.json").symlink_to(escapee)

    assert find_last_stop_file(root) is None
    assert read_last_stop(root) is None


def test_an_in_root_symlink_is_still_read(tmp_path: Path) -> None:
    # Containment, not a blanket ban on symlinks: a link that stays inside the
    # project root resolves in-root and must still be readable.
    root = tmp_path / "project"
    real = root / "artifacts"
    real.mkdir(parents=True)
    shutil.copy2(LANE_RESULT_FIXTURE, real / "TM-001.json")
    (root / ".factory").mkdir()
    (root / ".factory" / "results").symlink_to(real, target_is_directory=True)

    assert find_results_dir(root) is not None
    assert read_result(root, "TM-001") is not None


# --------------------------------------------------------------------------- #
# The module — and the port implementation wrapping it — are read-only
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("module", [runs_module, real_runs_module], ids=["runs", "real_runs"])
def test_module_source_has_no_filesystem_mutation(module: ModuleType) -> None:
    assert_module_is_read_only(module)


@pytest.mark.parametrize("module", [runs_module, real_runs_module], ids=["runs", "real_runs"])
def test_module_source_carries_the_read_only_header(module: ModuleType) -> None:
    assert_module_carries_read_only_header(module)
