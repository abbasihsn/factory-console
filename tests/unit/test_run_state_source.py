"""Unit tests for the source-aware run-state reader.

Exercises :func:`find_run_state_source` (probe order across the JSON and
directory forms, and the node type each location demands),
:func:`read_json_run_state` (the factory's nine ``FAC_STATES`` through the
explicit alias table, unrecognised statuses, and every malformed-file shape), and
:func:`probe_ticket_state_from_source` / :func:`run_state_resolver` (dispatch on
the resolved kind).

The JSON assertions run against the committed
``tests/fixtures/run_state/run-state.json``, which is written in the shape the
FACTORY writes — not one shaped to whatever this parser accepts. That is the
whole point: a fixture built from the same assumption as the code under test
cannot detect that the assumption is wrong, which is how the console came to ship
a marker-directory reader for a file the factory never writes. So the ground
truth here is the factory's: a ticket the factory recorded ``merged`` must read
back ``merged``, and a test asserting ``unknown`` there would be asserting the
bug.

The legacy directory form keeps its own suite in ``test_run_state.py``, which
stays green UNMODIFIED — that is the compatibility claim.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from factory_console.domain import JsonRunState, RunState, RunStateSource
from factory_console.file_adapter.run_state import (
    FACTORY_STATUS_ALIASES,
    find_run_state_dir,
    find_run_state_source,
    probe_ticket_state_from_source,
    read_json_run_state,
    run_state_resolver,
)

# The committed factory-shaped fixture, resolved relative to this file so the
# suite is path-independent.
JSON_FIXTURE = Path(__file__).parents[1] / "fixtures" / "run_state" / "run-state.json"

# The factory's own FAC_STATES vocabulary, restated here verbatim from the
# factory (via the T78 ticket's on-disk contract) rather than derived from the
# alias table — a table that drifted would otherwise agree with itself.
FAC_STATES = (
    "todo",
    "in_progress",
    "ready",
    "in_part",
    "in_submilestone",
    "merged",
    "flagged",
    "failed",
    "needs_human",
)

# Fixture ground truth, read off the fixture as the FACTORY wrote it.
FIXTURE_GROUND_TRUTH = {
    "T01": RunState.merged,
    "T03": RunState.merged,
    "T40": RunState.ready,
    "T56": RunState.in_progress,
    "T57": RunState.in_part,
    "T58": RunState.in_submilestone,
    "T74": RunState.flagged,
    "T75": RunState.failed,
    "T76": RunState.needs_human,
    "T77": RunState.todo,
}


def _place_json(root: Path, payload: str) -> Path:
    """Write ``payload`` to ``<root>/.factory/run-state.json`` and return the path."""
    factory_dir = root / ".factory"
    factory_dir.mkdir(parents=True, exist_ok=True)
    path = factory_dir / "run-state.json"
    path.write_text(payload, encoding="utf-8")
    return path


def _copy_fixture_json(root: Path) -> Path:
    """Copy the committed factory fixture to ``<root>/.factory/run-state.json``."""
    return _place_json(root, JSON_FIXTURE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# find_run_state_source — probe order across the two forms
# --------------------------------------------------------------------------- #


def test_json_beats_the_directory_when_both_are_present(tmp_path: Path) -> None:
    json_path = _copy_fixture_json(tmp_path)
    (tmp_path / ".factory" / "run-state").mkdir()

    source = find_run_state_source(tmp_path)
    assert source == RunStateSource(kind="json", path=json_path), (
        "the JSON file is what the factory writes today, so it must win over the "
        "legacy marker directory when both exist"
    )


def test_directory_is_resolved_when_only_it_is_present(tmp_path: Path) -> None:
    run_state_dir = tmp_path / ".factory" / "run-state"
    run_state_dir.mkdir(parents=True)

    assert find_run_state_source(tmp_path) == RunStateSource(
        kind="directory", path=run_state_dir
    ), "with no JSON file the legacy directory form must still resolve"


def test_docs_planning_directory_is_the_last_fallback(tmp_path: Path) -> None:
    fallback = tmp_path / "docs" / "planning" / ".run-state"
    fallback.mkdir(parents=True)

    assert find_run_state_source(tmp_path) == RunStateSource(kind="directory", path=fallback)


def test_no_source_resolves_to_none(tmp_path: Path) -> None:
    assert find_run_state_source(tmp_path) is None, (
        "with neither form present find_run_state_source must return None"
    )


# --------------------------------------------------------------------------- #
# find_run_state_source — containment, checked at RESOLUTION
# --------------------------------------------------------------------------- #


def test_a_json_source_resolving_outside_the_root_is_refused(tmp_path: Path) -> None:
    # ``is_file()`` follows symlinks, so without a containment check here the
    # source resolves and EVERY consumer reads it — list_tickets and
    # read_run_state included, not just the runs endpoint that probes containment
    # at its own read. The console would then report the source as not found
    # while serving ticket states parsed out of it.
    root = tmp_path / "project"
    (root / ".factory").mkdir(parents=True)
    outside = tmp_path / "outside-run-state.json"
    outside.write_text(json.dumps({"tickets": {"T01": {"status": "merged"}}}), encoding="utf-8")
    (root / ".factory" / "run-state.json").symlink_to(outside)

    assert find_run_state_source(root) is None, (
        "a run-state.json resolving outside the project root must not resolve to a "
        "source at all, so no consumer can read it"
    )


def test_a_directory_source_resolving_outside_the_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / ".factory").mkdir(parents=True)
    outside = tmp_path / "outside-run-state"
    (outside / "merged").mkdir(parents=True)
    (root / ".factory" / "run-state").symlink_to(outside, target_is_directory=True)

    assert find_run_state_source(root) is None, (
        "the marker-directory form gets the same containment rule as the JSON form"
    )


def test_an_escaping_source_does_not_fall_through_to_a_lower_location(tmp_path: Path) -> None:
    # A project whose highest-precedence run-state escapes the root has an
    # UNREADABLE run-state, not a different one — silently answering from the
    # fallback would report states the factory did not write there.
    root = tmp_path / "project"
    (root / ".factory").mkdir(parents=True)
    outside = tmp_path / "outside-run-state.json"
    outside.write_text(json.dumps({"tickets": {}}), encoding="utf-8")
    (root / ".factory" / "run-state.json").symlink_to(outside)
    (root / "docs" / "planning" / ".run-state").mkdir(parents=True)

    assert find_run_state_source(root) is None


def test_an_in_root_symlink_still_resolves(tmp_path: Path) -> None:
    # Containment is about where a path RESOLVES, not whether it is a symlink:
    # an in-root link is a legitimate layout and must not be refused.
    root = tmp_path / "project"
    (root / ".factory").mkdir(parents=True)
    real = root / "actual-run-state.json"
    real.write_text(json.dumps({"tickets": {}}), encoding="utf-8")
    link = root / ".factory" / "run-state.json"
    link.symlink_to(real)

    assert find_run_state_source(root) == RunStateSource(kind="json", path=link)


# --------------------------------------------------------------------------- #
# find_run_state_source — each location demands the right NODE TYPE
# --------------------------------------------------------------------------- #


def test_a_directory_named_run_state_json_is_not_accepted_as_the_json_source(
    tmp_path: Path,
) -> None:
    (tmp_path / ".factory" / "run-state.json").mkdir(parents=True)
    fallback = tmp_path / "docs" / "planning" / ".run-state"
    fallback.mkdir(parents=True)

    assert find_run_state_source(tmp_path) == RunStateSource(kind="directory", path=fallback), (
        "a DIRECTORY at the json location cannot be parsed as JSON, so the probe "
        "must skip it rather than resolve an unreadable source"
    )


def test_a_file_at_the_directory_location_is_not_accepted_as_the_directory_source(
    tmp_path: Path,
) -> None:
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    (factory_dir / "run-state").write_text("not a directory", encoding="utf-8")
    fallback = tmp_path / "docs" / "planning" / ".run-state"
    fallback.mkdir(parents=True)

    assert find_run_state_source(tmp_path) == RunStateSource(kind="directory", path=fallback), (
        "a plain FILE where the marker directory belongs holds no markers, so the "
        "probe must fall through to the next location"
    )


# --------------------------------------------------------------------------- #
# find_run_state_dir — the directory-only wrapper
# --------------------------------------------------------------------------- #


def test_find_run_state_dir_returns_none_for_a_json_source(tmp_path: Path) -> None:
    _copy_fixture_json(tmp_path)
    assert find_run_state_dir(tmp_path) is None, (
        "a JSON-sourced project has no run-state DIRECTORY, so the directory-only "
        "wrapper must say so rather than invent a path"
    )


def test_find_run_state_dir_returns_the_path_of_a_directory_source(tmp_path: Path) -> None:
    run_state_dir = tmp_path / ".factory" / "run-state"
    run_state_dir.mkdir(parents=True)
    assert find_run_state_dir(tmp_path) == run_state_dir


# --------------------------------------------------------------------------- #
# read_json_run_state — the factory's vocabulary through the alias table
# --------------------------------------------------------------------------- #


def test_the_alias_table_covers_exactly_the_factory_nine_states() -> None:
    assert set(FACTORY_STATUS_ALIASES) == set(FAC_STATES), (
        "the alias table must name every FAC_STATES value and nothing else"
    )


@pytest.mark.parametrize("status", FAC_STATES)
def test_every_factory_status_maps_to_a_known_state(status: str, tmp_path: Path) -> None:
    path = _place_json(tmp_path, json.dumps({"version": 1, "tickets": {"T01": {"status": status}}}))

    parsed = read_json_run_state(path)
    assert parsed.states["T01"] is not RunState.unknown, (
        f"{status!r} is a real factory status and must map to a console state"
    )
    # Every factory status has a member whose VALUE is that status verbatim — the
    # enum mirrors the name its source used, so nothing is lost in translation.
    assert parsed.states["T01"].value == status
    assert parsed.unrecognised == []


def test_the_committed_factory_fixture_reads_back_as_the_factory_wrote_it() -> None:
    parsed = read_json_run_state(JSON_FIXTURE)

    assert {
        ticket_id: parsed.states[ticket_id] for ticket_id in FIXTURE_GROUND_TRUTH
    } == FIXTURE_GROUND_TRUTH
    assert parsed.unrecognised == []


def test_pr_urls_are_read_from_the_same_entries_as_the_statuses() -> None:
    # The runs endpoint (T81) needs the PR url, and it comes out of THIS parse so
    # ``run-state.json`` keeps exactly one reader. Asserted against the committed
    # factory fixture, which carries both ``pr_url`` forms.
    parsed = read_json_run_state(JSON_FIXTURE)

    assert parsed.pr_urls["T01"] == "https://github.com/abbasihsn/factory-console/pull/1"
    assert parsed.pr_urls["T74"] == "https://github.com/abbasihsn/factory-console/pull/173"
    # ``pr_url: null`` is the factory's "no PR yet": absent from the map, never a
    # key whose value is ``None``.
    assert "T03" not in parsed.pr_urls
    assert set(parsed.pr_urls) == {"T01", "T02", "T40", "T74"}


def test_a_pr_url_survives_a_status_this_console_does_not_know(tmp_path: Path) -> None:
    # The url is collected before the status checks can skip the entry: a tenth
    # factory state must not also lose the PR link.
    path = _place_json(
        tmp_path,
        json.dumps(
            {"version": 1, "tickets": {"T01": {"status": "in_orbit", "pr_url": "https://x/1"}}}
        ),
    )

    parsed = read_json_run_state(path)

    assert parsed.states == {}
    assert parsed.unrecognised == ["in_orbit"]
    assert parsed.pr_urls == {"T01": "https://x/1"}


def test_the_fixture_is_not_read_as_the_directory_form() -> None:
    # The measured bug: against a JSON-sourced project the directory prober found
    # nothing and reported unknown for tickets the factory had merged.
    source = RunStateSource(kind="json", path=JSON_FIXTURE)
    assert probe_ticket_state_from_source(source, "T01") is RunState.merged


def test_an_unknown_status_yields_unknown_and_is_reported(tmp_path: Path) -> None:
    path = _place_json(
        tmp_path,
        json.dumps(
            {
                "version": 1,
                "tickets": {
                    "T01": {"status": "merged", "pr_url": None},
                    "T02": {"status": "in_orbit", "pr_url": None},
                    "T03": {"status": "in_orbit", "pr_url": None},
                },
            }
        ),
    )

    parsed = read_json_run_state(path)
    assert parsed.states == {"T01": RunState.merged}
    assert parsed.unrecognised == ["in_orbit"], (
        "a tenth factory status must be reported as a named gap (once), not "
        "silently dropped into a repo full of unknown"
    )
    source = RunStateSource(kind="json", path=path)
    assert probe_ticket_state_from_source(source, "T02") is RunState.unknown


def test_a_hyphenated_status_is_not_munged_into_an_underscored_member(tmp_path: Path) -> None:
    # ``in-flight`` is the DIRECTORY form's name and does not exist in the
    # factory. It must not be string-massaged into RunState.in_progress.
    path = _place_json(
        tmp_path, json.dumps({"version": 1, "tickets": {"T01": {"status": "in-flight"}}})
    )

    parsed = read_json_run_state(path)
    assert parsed.states == {}
    assert parsed.unrecognised == ["in-flight"]


# --------------------------------------------------------------------------- #
# read_json_run_state — malformed input never fails the request
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("{not json at all", id="unparseable"),
        pytest.param(json.dumps({"version": 1}), id="tickets-absent"),
        pytest.param(json.dumps({"version": 1, "tickets": ["T01", "T02"]}), id="tickets-as-list"),
        pytest.param(json.dumps({"version": 1, "tickets": None}), id="tickets-null"),
        pytest.param(json.dumps(["T01"]), id="document-not-an-object"),
        pytest.param("", id="empty-file"),
        # Deep nesting makes ``json.loads`` raise ``RecursionError``, NOT a
        # ``JSONDecodeError``. This artifact is written by another process, so
        # the "never raises" contract has to hold for the exceptions pathological
        # input actually produces — otherwise one bad file 500s every
        # list/read/write request until it changes.
        pytest.param("[" * 20_000 + "]" * 20_000, id="too-deeply-nested"),
    ],
)
def test_a_malformed_file_yields_unknown_for_every_ticket_without_raising(
    payload: str, tmp_path: Path
) -> None:
    path = _place_json(tmp_path, payload)

    parsed = read_json_run_state(path)
    assert parsed == JsonRunState(), (
        "a broken run-state file is a source-level problem, not a request failure: "
        "every ticket must resolve unknown"
    )
    source = RunStateSource(kind="json", path=path)
    for ticket_id in ("T01", "T02"):
        assert probe_ticket_state_from_source(source, ticket_id) is RunState.unknown


def test_an_entry_without_a_usable_status_is_skipped_but_its_siblings_survive(
    tmp_path: Path,
) -> None:
    path = _place_json(
        tmp_path,
        json.dumps(
            {
                "version": 1,
                "tickets": {
                    "T01": {"status": "merged"},
                    "T02": "merged",
                    "T03": {"pr_url": None},
                    "T04": {"status": 7},
                },
            }
        ),
    )

    parsed = read_json_run_state(path)
    assert parsed.states == {"T01": RunState.merged}
    assert parsed.unrecognised == []


def test_an_unreadable_json_source_yields_unknown(tmp_path: Path) -> None:
    missing = tmp_path / ".factory" / "run-state.json"
    assert read_json_run_state(missing) == JsonRunState(), (
        "a file that vanished between discovery and read must degrade to unknown, "
        "not raise OSError out of the request"
    )


# --------------------------------------------------------------------------- #
# probe_ticket_state_from_source / run_state_resolver — dispatch on the kind
# --------------------------------------------------------------------------- #


def test_a_none_source_resolves_to_unknown() -> None:
    assert probe_ticket_state_from_source(None, "T01") is RunState.unknown


def test_a_ticket_absent_from_the_json_resolves_to_unknown() -> None:
    source = RunStateSource(kind="json", path=JSON_FIXTURE)
    assert probe_ticket_state_from_source(source, "T99-absent") is RunState.unknown


def test_a_directory_source_delegates_to_the_marker_prober(tmp_path: Path) -> None:
    run_state_dir = tmp_path / ".factory" / "run-state"
    (run_state_dir / "merged").mkdir(parents=True)
    (run_state_dir / "merged" / "CAD-100").write_text("", encoding="utf-8")
    source = RunStateSource(kind="directory", path=run_state_dir)

    assert probe_ticket_state_from_source(source, "CAD-100") is RunState.merged
    # The directory form's "present dir, no marker" default is unchanged.
    assert probe_ticket_state_from_source(source, "CAD-999") is RunState.todo


def test_the_resolver_agrees_with_the_single_ticket_probe(tmp_path: Path) -> None:
    json_path = _copy_fixture_json(tmp_path)
    source = RunStateSource(kind="json", path=json_path)
    resolve = run_state_resolver(source)

    for ticket_id, expected in FIXTURE_GROUND_TRUTH.items():
        assert resolve(ticket_id) is expected
        assert probe_ticket_state_from_source(source, ticket_id) is expected


def test_the_resolver_reads_the_json_once(tmp_path: Path) -> None:
    # The batch resolver exists so a 77-ticket listing parses the file once. Prove
    # it by deleting the file after the resolver is built: a resolver that re-read
    # per ticket would start answering unknown.
    json_path = _copy_fixture_json(tmp_path)
    resolve = run_state_resolver(RunStateSource(kind="json", path=json_path))
    json_path.unlink()

    assert resolve("T01") is RunState.merged


# --------------------------------------------------------------------------- #
# GUARD — reading never mutates the committed fixture
# --------------------------------------------------------------------------- #


def test_reading_the_fixture_leaves_it_byte_identical(tmp_path: Path) -> None:
    before = shutil.copy2(JSON_FIXTURE, tmp_path / "before.json")

    read_json_run_state(JSON_FIXTURE)
    probe_ticket_state_from_source(RunStateSource(kind="json", path=JSON_FIXTURE), "T01")

    assert JSON_FIXTURE.read_bytes() == Path(before).read_bytes()
    assert JSON_FIXTURE.stat().st_mtime == Path(before).stat().st_mtime
