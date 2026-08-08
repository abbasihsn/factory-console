"""Unit tests for the lane PHASE and the open SUB-VERSION, both read from run-state.

Two additive reads of ``.factory/run-state.json``, and the tests are together because
the risk they guard is the same one: neither field may disturb the run-state resolution
beside it. A phase is displayed and gated on by nothing; a sub-version names the branch
the factory is holding at. Either one going wrong must cost information, never a write.

The sharp case has its own section below — an UNRECOGNISED phase must not escalate the
way an unrecognised status does, or a cosmetic field becomes a project-wide write
lockout.
"""

from __future__ import annotations

import json
from pathlib import Path

from factory_console.domain import RunState
from factory_console.domain.run_state_source import RunStateSource
from factory_console.file_adapter.run_state import (
    probe_lane_phase_from_source,
    probe_ticket_state_from_source,
    read_json_run_state,
    read_subversion,
    run_state_and_phase_resolvers,
)


def _source(tmp_path: Path, document: object) -> RunStateSource:
    """Write ``document`` as a JSON run-state file and return its source."""
    path = tmp_path / "run-state.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return RunStateSource(kind="json", path=path)


def _directory_source(tmp_path: Path) -> RunStateSource:
    """A legacy marker-directory source — the v2 layout, which records no phase."""
    directory = tmp_path / "run-state"
    (directory / "merged").mkdir(parents=True)
    (directory / "merged" / "T01").write_text("", encoding="utf-8")
    return RunStateSource(kind="directory", path=directory)


# --------------------------------------------------------------------------- #
# phase — what an in_progress lane is actually doing
# --------------------------------------------------------------------------- #


def test_a_recorded_phase_is_read(tmp_path: Path) -> None:
    source = _source(
        tmp_path, {"tickets": {"T01": {"status": "in_progress", "phase": "reviewing"}}}
    )

    assert probe_lane_phase_from_source(source, "T01") == "reviewing"


def test_every_phase_the_factory_writes_reads_back(tmp_path: Path) -> None:
    # `FAC_LANE_PHASES` in app-factory's lib/state.sh, in its order. Pinned as a set so
    # a phase the factory adds shows up here as a gap rather than as a silent blank.
    phases = ["building", "accepting", "reviewing", "fixing", "verifying"]
    source = _source(
        tmp_path,
        {
            "tickets": {
                f"T{n:02d}": {"status": "in_progress", "phase": phase}
                for n, phase in enumerate(phases, start=1)
            }
        },
    )

    read = [probe_lane_phase_from_source(source, f"T{n:02d}") for n in range(1, len(phases) + 1)]

    assert read == phases


def test_an_explicit_null_phase_is_simply_no_phase(tmp_path: Path) -> None:
    # The factory writes `phase: null` on EVERY status transition rather than deleting
    # the key, so this is the ordinary shape of a ticket that is not mid-lane — not a
    # malformed entry, and it must not be collected as one.
    source = _source(tmp_path, {"tickets": {"T01": {"status": "merged", "phase": None}}})

    assert probe_lane_phase_from_source(source, "T01") is None
    assert probe_ticket_state_from_source(source, "T01") is RunState.merged


def test_an_absent_phase_key_is_no_phase(tmp_path: Path) -> None:
    source = _source(tmp_path, {"tickets": {"T01": {"status": "todo"}}})

    assert probe_lane_phase_from_source(source, "T01") is None


def test_a_blank_phase_is_no_phase(tmp_path: Path) -> None:
    # An empty string answers nothing and would render as a qualifier with no word in it.
    source = _source(tmp_path, {"tickets": {"T01": {"status": "in_progress", "phase": "   "}}})

    assert probe_lane_phase_from_source(source, "T01") is None


def test_an_id_the_source_does_not_name_has_no_phase(tmp_path: Path) -> None:
    source = _source(tmp_path, {"tickets": {"T01": {"status": "in_progress", "phase": "fixing"}}})

    assert probe_lane_phase_from_source(source, "T99") is None


def test_a_marker_directory_records_no_phase(tmp_path: Path) -> None:
    # Not a degradation: the v2 layout records a state as a FILE'S EXISTENCE and has
    # nowhere to put a phase, so "no phase" is the complete reading of such a source.
    source = _directory_source(tmp_path)

    assert probe_ticket_state_from_source(source, "T01") is RunState.merged
    assert probe_lane_phase_from_source(source, "T01") is None


def test_a_project_with_no_source_has_no_phase() -> None:
    assert probe_lane_phase_from_source(None, "T01") is None


def test_an_unreadable_source_has_no_phase_and_still_refuses_the_write(tmp_path: Path) -> None:
    # The two answers degrade in OPPOSITE directions, deliberately. The state fails
    # CLOSED — a file that will not open may say `merged`, so every write is refused.
    # The phase fails to nothing, because there is no refusing phase to fail into and
    # nothing gates on one.
    path = tmp_path / "run-state.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o000)
    source = RunStateSource(kind="json", path=path)
    try:
        assert probe_ticket_state_from_source(source, "T01") is RunState.unreadable
        assert probe_lane_phase_from_source(source, "T01") is None
    finally:
        path.chmod(0o644)


# --------------------------------------------------------------------------- #
# A phase must never become a write lockout
# --------------------------------------------------------------------------- #


def test_an_unrecognised_phase_is_carried_through_verbatim(tmp_path: Path) -> None:
    # The OPPOSITE of an unrecognised STATUS, which resolves the refusing `unreadable`.
    # A phase is displayed and never branched on, so carrying it costs an odd label
    # while dropping it would blank a field the operator is watching. Same reasoning as
    # `LevelSpend.level`, a free `str` so an unrecognised level appears rather than
    # vanishing.
    source = _source(
        tmp_path, {"tickets": {"T01": {"status": "in_progress", "phase": "auditing"}}}
    )

    assert probe_lane_phase_from_source(source, "T01") == "auditing"


def test_an_unrecognised_phase_leaves_the_status_alone(tmp_path: Path) -> None:
    # THE test this file exists for. Escalating an unknown phase the way an unknown
    # status is escalated would refuse every write on a ticket whose status read
    # perfectly — turning a cosmetic field into a project-wide lockout.
    source = _source(
        tmp_path, {"tickets": {"T01": {"status": "todo", "phase": "not-a-real-phase"}}}
    )

    assert probe_ticket_state_from_source(source, "T01") is RunState.todo
    assert probe_lane_phase_from_source(source, "T01") == "not-a-real-phase"


def test_a_ticket_whose_status_is_unclassifiable_contributes_no_phase(tmp_path: Path) -> None:
    # `phases` must never describe a ticket `states` could not: the phase is read only
    # after the status classifies, so an entry this console could not interpret does not
    # get to half-report.
    source = _source(
        tmp_path, {"tickets": {"T01": {"status": "in_review", "phase": "reviewing"}}}
    )

    assert probe_ticket_state_from_source(source, "T01") is RunState.unreadable
    assert probe_lane_phase_from_source(source, "T01") is None


def test_a_phase_beside_a_finished_status_is_still_reported(tmp_path: Path) -> None:
    # Only reachable by a hand-edit — the factory clears the phase on every transition.
    # The READER carries it anyway: dropping it here would destroy the only evidence of
    # the inconsistency. Where a phase is worth showing is the view's decision.
    source = _source(tmp_path, {"tickets": {"T01": {"status": "merged", "phase": "reviewing"}}})

    assert probe_ticket_state_from_source(source, "T01") is RunState.merged
    assert probe_lane_phase_from_source(source, "T01") == "reviewing"


# --------------------------------------------------------------------------- #
# Both answers from one read
# --------------------------------------------------------------------------- #


def test_the_paired_resolvers_agree_with_the_single_ticket_forms(tmp_path: Path) -> None:
    # The paired form exists to avoid parsing the file twice for one request; it must
    # not become a second opinion in the process.
    document = {
        "tickets": {
            "T01": {"status": "in_progress", "phase": "building"},
            "T02": {"status": "merged", "phase": None},
            "T03": {"status": "in_review"},
        }
    }
    source = _source(tmp_path, document)

    resolve_state, resolve_phase = run_state_and_phase_resolvers(source)

    for ticket_id in ("T01", "T02", "T03", "T99"):
        assert resolve_state(ticket_id) is probe_ticket_state_from_source(source, ticket_id)
        assert resolve_phase(ticket_id) == probe_lane_phase_from_source(source, ticket_id)


def test_the_paired_resolvers_answer_a_directory_source_too(tmp_path: Path) -> None:
    # The non-JSON leg delegates its state answer to `run_state_resolver` verbatim — the
    # directory form's vacuity scan and log-once discipline must have one implementation.
    source = _directory_source(tmp_path)

    resolve_state, resolve_phase = run_state_and_phase_resolvers(source)

    assert resolve_state("T01") is RunState.merged
    assert resolve_phase("T01") is None


# --------------------------------------------------------------------------- #
# subversion — the one recurring human gate
# --------------------------------------------------------------------------- #


def test_an_open_subversion_is_read_with_every_field(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        {
            "tickets": {},
            "subversion": {
                "branch": "factory/v1.0",
                "base_sha": "abc1234",
                "name": "v1.0",
                "pr_url": "https://github.com/o/r/pull/7",
            },
        },
    )

    subversion = read_subversion(source)

    assert subversion is not None
    assert subversion.branch == "factory/v1.0"
    assert subversion.baseSha == "abc1234"
    assert subversion.name == "v1.0"
    assert subversion.prUrl == "https://github.com/o/r/pull/7"


def test_a_subversion_cut_but_not_yet_pr_ed_reads_with_a_null_url(tmp_path: Path) -> None:
    # The factory records the branch when it CUTS the sub-version and the url only once
    # `ai-gh-open-pr` has run, so this gap is the ordinary mid-life shape of the record.
    # A branch with no url is a sub-version being BUILT; one with a url is a sub-version
    # WAITING on a human — the two things an operator most wants to tell apart.
    source = _source(
        tmp_path,
        {
            "tickets": {},
            "subversion": {
                "branch": "factory/v1.1",
                "base_sha": "def5678",
                "name": "v1.1",
                "pr_url": None,
            },
        },
    )

    subversion = read_subversion(source)

    assert subversion is not None
    assert subversion.branch == "factory/v1.1"
    assert subversion.prUrl is None


def test_no_subversion_record_is_none(tmp_path: Path) -> None:
    # The NORMAL state between cuts — the factory deletes the record when the branch
    # lands on main. Not a failure, and a view must render it as nothing at all.
    source = _source(tmp_path, {"tickets": {"T01": {"status": "merged"}}})

    assert read_subversion(source) is None


def test_a_subversion_with_no_branch_is_none(tmp_path: Path) -> None:
    # The branch is the one field the record is useless without: it names the thing
    # being held, and a strip with no branch would be an empty box announcing a gate it
    # cannot identify.
    source = _source(tmp_path, {"tickets": {}, "subversion": {"name": "v1.0"}})

    assert read_subversion(source) is None


def test_a_subversion_that_is_not_an_object_is_none(tmp_path: Path) -> None:
    source = _source(tmp_path, {"tickets": {}, "subversion": "factory/v1.0"})

    assert read_subversion(source) is None


def test_a_marker_directory_has_no_subversion(tmp_path: Path) -> None:
    # v2 had no sub-version to hold at.
    assert read_subversion(_directory_source(tmp_path)) is None


def test_no_source_has_no_subversion() -> None:
    assert read_subversion(None) is None


def test_an_unparseable_source_has_no_subversion(tmp_path: Path) -> None:
    # Answers None rather than failing closed, and that IS the considered answer: the
    # only consumer is a header strip, so a wrong None costs a link nobody sees while a
    # wrong non-None would name a branch nobody cut. There is nothing to fail closed
    # into — a refusing sentinel would still have to be rendered as a claim.
    path = tmp_path / "run-state.json"
    path.write_text("{not json", encoding="utf-8")

    assert read_subversion(RunStateSource(kind="json", path=path)) is None


def test_reading_a_subversion_does_not_disturb_the_ticket_states(tmp_path: Path) -> None:
    # The two live in one document and are read by two functions; neither may consume or
    # invalidate the other.
    source = _source(
        tmp_path,
        {
            "tickets": {"T01": {"status": "in_progress", "phase": "verifying"}},
            "subversion": {"branch": "factory/v1.0", "base_sha": "abc", "name": "v1.0"},
        },
    )

    assert read_subversion(source) is not None
    assert probe_ticket_state_from_source(source, "T01") is RunState.in_progress
    assert probe_lane_phase_from_source(source, "T01") == "verifying"
    assert read_json_run_state(source.path).states == {"T01": RunState.in_progress}
