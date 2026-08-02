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
import os
import shutil
from pathlib import Path

import pytest

from factory_console.domain import JsonRunState, RunState, RunStateSource
from factory_console.file_adapter.run_state import (
    FACTORY_STATUS_ALIASES,
    find_run_state_dir,
    find_run_state_source,
    probe_ticket_state_from_source,
    probe_ticket_state_with_reason,
    read_json_run_state,
    run_state_resolver,
)
from factory_console.file_adapter.write_gate import (
    DELETABLE_STATES,
    MUTABLE_STATES,
    TicketNotMutable,
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


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permission bits")
def test_a_candidate_that_cannot_be_probed_resolves_to_it_and_refuses(tmp_path: Path) -> None:
    # T80's RESOLUTION INVARIANT (amendment 3) applied one step UPSTREAM of resolution.
    # `.factory` mode 0000 means the probe cannot tell whether `run-state.json` is
    # there — and a candidate we could not look at must not be skipped, because
    # skipping it falls through to a lower-precedence location or to `None`, and
    # `None` is the MUTABLE `unknown` for EVERY ticket in the project. So the
    # unprobeable candidate becomes the source and the read path refuses. The readable
    # fallback below is the point: it must NOT win, or a project could hide a merged
    # ticket behind an unreadable higher-precedence source.
    #
    # Version-independence is asserted here too, implicitly: `Path.is_file()` re-raises
    # EACCES through CPython 3.12 (an unmapped 500 out of a read-only prober) and
    # SWALLOWS it from 3.13 (gh-113978 — silently "no run-state source", i.e. every
    # ticket mutable). Neither is a decision, and the module's own errno split is what
    # makes this one.
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    (factory_dir / "run-state.json").write_text(
        json.dumps({"version": 1, "tickets": {"T01": {"status": "merged"}}}), encoding="utf-8"
    )
    fallback = tmp_path / "docs" / "planning" / ".run-state"
    (fallback / "todo").mkdir(parents=True)
    (fallback / "todo" / "T01").write_text("", encoding="utf-8")
    factory_dir.chmod(0o000)
    try:
        source = find_run_state_source(tmp_path)
        assert source is not None
        states = [probe_ticket_state_from_source(source, tid) for tid in ("T01", "T99")]
    finally:
        factory_dir.chmod(0o755)

    assert source == RunStateSource(kind="json", path=factory_dir / "run-state.json")
    assert states == [RunState.unreadable, RunState.unreadable]
    # Asserted as the gate consequence, not as wording: refused for edit AND delete.
    assert all(state not in MUTABLE_STATES for state in states)
    assert all(state not in DELETABLE_STATES for state in states)


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


def test_the_fixture_is_not_read_as_the_directory_form() -> None:
    # The measured bug: against a JSON-sourced project the directory prober found
    # nothing and reported unknown for tickets the factory had merged.
    source = RunStateSource(kind="json", path=JSON_FIXTURE)
    assert probe_ticket_state_from_source(source, "T01") is RunState.merged


def test_an_unknown_status_refuses_and_is_reported(tmp_path: Path) -> None:
    # SUPERSEDED by T80 amendment 4 — this used to assert `unknown` (mutable), and the
    # reasoning that made that look right is kept because it is half true: a status the
    # console cannot classify IS a named gap, and collecting it into `unrecognised` IS
    # how a factory that gained a tenth FAC_STATES member becomes visible instead of
    # vanishing into a repo full of `unknown`. What was wrong was acting on it: naming
    # the gap in a log line and then handing the write gate a MUTABLE state ignores it
    # at the only point that matters. `in_orbit` is the factory claiming something about
    # T02 in a vocabulary this console does not speak, and a claim we could not read is
    # not silence — so the refusal and the report are BOTH required, which is why this
    # test still asserts `unrecognised` unchanged.
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
    # De-duplicated for the FILE, but recorded per TICKET — the refusal has to name the
    # value for one id, and `unrecognised` cannot say which value belonged to whom.
    assert parsed.unclassifiable == {"T02": "status 'in_orbit'", "T03": "status 'in_orbit'"}

    source = RunStateSource(kind="json", path=path)
    assert probe_ticket_state_from_source(source, "T02") is RunState.unreadable
    assert probe_ticket_state_from_source(source, "T02") not in MUTABLE_STATES, (
        "an entry naming this ticket under a status the console cannot classify must "
        "refuse: the status we failed to read could have been `merged`"
    )
    # The sibling that DID classify is untouched — the refusal is per entry, never
    # per file, or one tenth state would lock a whole project read-only.
    assert probe_ticket_state_from_source(source, "T01") is RunState.merged


def test_the_unclassifiable_refusal_names_the_value_it_could_not_read(tmp_path: Path) -> None:
    # T80 amendment 4, step 1: "the refusal names the unrecognised value — an operator
    # needs *the run-state says `in_review`, which this console does not know*, not
    # *not tracked*". The two `unreadable` causes share a state (same authorization
    # answer) and must NOT share prose (different remedy): one is fixed by chmod, this
    # one by a console that knows the status the factory now writes.
    path = _place_json(
        tmp_path,
        json.dumps({"version": 1, "tickets": {"T01": {"status": "in_review"}}}),
    )
    source = RunStateSource(kind="json", path=path)

    state, unclassifiable = probe_ticket_state_with_reason(source, "T01")
    assert state is RunState.unreadable
    assert unclassifiable == "status 'in_review'"

    refusal = TicketNotMutable("T01", state, source_path=path, unclassifiable=unclassifiable)
    assert "in_review" in refusal.message
    assert "could not be read" not in refusal.message, (
        "the file read fine; pointing the operator at permissions is the wrong fix"
    )

    # An id the source does not name at all has no value to report, and an id in a
    # source that could not be read has none either — both keep the generic prose.
    assert probe_ticket_state_with_reason(source, "T99-no-entry")[1] is None


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
    assert parsed == JsonRunState(readable=False), (
        "a broken run-state file is a source-level problem, not a request failure: "
        "every ticket must resolve unknown, not absent (readable=False, not just empty)"
    )
    source = RunStateSource(kind="json", path=path)
    for ticket_id in ("T01", "T02"):
        assert probe_ticket_state_from_source(source, ticket_id) is RunState.unknown


def test_non_utf8_bytes_stay_unknown_rather_than_unreadable(tmp_path: Path) -> None:
    # The boundary of T80 amendment 2 on the JSON form, and the guard against
    # over-widening it: these bytes WERE read, they simply are not text this console
    # can decode. That is a CONTENT problem, exactly like the invalid JSON above, and
    # content problems keep the mutable `unknown` — only a failure to read the bytes
    # at all fails closed. A file the factory wrote in the wrong encoding must not
    # turn the whole project read-only.
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir(parents=True)
    path = factory_dir / "run-state.json"
    path.write_bytes(b'{"version": 1, "tickets": {"T\xff1": {}}}')

    parsed = read_json_run_state(path)
    assert parsed.readable is False
    assert parsed.unreadable is False

    source = RunStateSource(kind="json", path=path)
    assert probe_ticket_state_from_source(source, "T01") is RunState.unknown


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
    # Only a STRING status outside the alias table is a named gap in the factory's
    # vocabulary; a missing or non-string status is a malformed entry, not a tenth
    # state, so it must not pollute the list an operator reads as "your console is a
    # version behind". Both still refuse — see below.
    assert parsed.unrecognised == []
    # T80: a skipped entry still HAD an entry, so its id must land in
    # ``known_ticket_ids`` — that is the only thing separating "listed under
    # something we could not classify" from "not listed at all" (absent).
    assert parsed.known_ticket_ids == frozenset({"T01", "T02", "T03", "T04"})
    # T80 amendment 4: and WHAT could not be classified, per id, so the refusal can
    # name it. Each phrase describes the actual shape found, because the offending
    # value is not always a string — `{"T02": "merged"}` puts a `str` where the object
    # belongs, `{"status": 7}` an `int` where the status does.
    assert parsed.unclassifiable == {
        "T02": "an entry that is not an object",
        "T03": "an entry with no status",
        "T04": "an entry whose status is not a string (int)",
    }

    # SUPERSEDED by T80 amendment 4 — these three asserted the mutable `unknown`. The
    # old reasoning was that a skipped entry is indistinguishable from silence once its
    # status is gone; what it missed is that the ENTRY is the claim, and this file names
    # T02/T03/T04 whether or not the console can read what it says about them. The
    # regression this still guards is the same one, in the same direction: moving the
    # ``known_ticket_ids.add`` below the ``continue`` would flip these three from a
    # refusal to `absent` — a different 409 for edit, but a PERMITTED delete, since
    # `absent` is deletable and `unreadable` is not.
    source = RunStateSource(kind="json", path=path)
    for ticket_id in ("T02", "T03", "T04"):
        assert probe_ticket_state_from_source(source, ticket_id) is RunState.unreadable
        assert probe_ticket_state_from_source(source, ticket_id) not in DELETABLE_STATES
    assert probe_ticket_state_from_source(source, "T99-no-entry-at-all") is RunState.absent


def test_an_empty_tickets_object_yields_unknown_for_every_ticket(tmp_path: Path) -> None:
    # T80's amendment, gap 1, on the JSON form: ``tickets: {}`` parsed fine — the file
    # is READABLE, it simply names nobody — and a source that names nobody says nothing
    # about anybody. Answering `absent` here would 409 every write in the project.
    path = _place_json(tmp_path, json.dumps({"version": 1, "tickets": {}}))

    parsed = read_json_run_state(path)
    assert parsed.readable is True, "an empty tickets object is a VALID file, not a broken one"
    assert parsed.known_ticket_ids == frozenset()

    source = RunStateSource(kind="json", path=path)
    for ticket_id in ("T01", "T99", "CAD-118"):
        assert probe_ticket_state_from_source(source, ticket_id) is RunState.unknown


def test_a_single_entry_still_makes_other_ids_absent(tmp_path: Path) -> None:
    # The regression guard for the amendment: one entry is enough for the source to
    # exercise authority, so an id it does not list is `absent` (refused), exactly as
    # before. This is the test that fails if the vacuous rule over-corrects.
    path = _place_json(
        tmp_path, json.dumps({"version": 1, "tickets": {"T01": {"status": "merged"}}})
    )
    source = RunStateSource(kind="json", path=path)

    assert probe_ticket_state_from_source(source, "T01") is RunState.merged
    assert probe_ticket_state_from_source(source, "T02") is RunState.absent


def test_a_vanished_json_source_yields_unknown(tmp_path: Path) -> None:
    # A file that is NOT THERE — nothing exists to be hiding a `merged` entry, so this
    # is indistinguishable from a project with no source at all and stays MUTABLE. The
    # name matters: this is the vanished case, not the unreadable one below, and T80's
    # second amendment turns on telling the two apart.
    missing = tmp_path / ".factory" / "run-state.json"
    parsed = read_json_run_state(missing)
    assert parsed == JsonRunState(readable=False), (
        "a file that vanished between discovery and read must degrade to unknown, "
        "not raise OSError out of the request"
    )
    assert parsed.unreadable is False

    source = RunStateSource(kind="json", path=missing)
    for ticket_id in ("T01", "T99"):
        assert probe_ticket_state_from_source(source, ticket_id) is RunState.unknown
    assert run_state_resolver(source)("T01") is RunState.unknown


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
def test_a_json_source_that_cannot_be_read_yields_unreadable(tmp_path: Path) -> None:
    # T80 amendment 2, the JSON form's half. The file EXISTS and its bytes cannot be
    # read (chmod 000 — the factory wrote it under a different uid). "I could not
    # look", never "there is nothing to find": it may be recording this ticket as
    # `merged`, so every id resolves the refusing `unreadable` and BOTH write gates
    # say no. Before this, the same EACCES resolved the mutable `unknown` and granted
    # the write precisely because the check could not run.
    path = _place_json(
        tmp_path, json.dumps({"version": 1, "tickets": {"T01": {"status": "merged"}}})
    )
    path.chmod(0o000)
    source = RunStateSource(kind="json", path=path)
    try:
        parsed = read_json_run_state(path)
        probed = probe_ticket_state_from_source(source, "T01")
        resolved = [run_state_resolver(source)(tid) for tid in ("T01", "T99")]
    finally:
        path.chmod(0o644)

    assert parsed.readable is False
    assert parsed.unreadable is True
    # The single-ticket prober and the batch resolver must not answer differently.
    assert probed is RunState.unreadable
    assert resolved == [RunState.unreadable, RunState.unreadable]
    # Distinguishable from `absent` on the STATE, and refused for edit AND delete.
    assert probed is not RunState.absent
    assert probed not in MUTABLE_STATES
    assert probed not in DELETABLE_STATES


# --------------------------------------------------------------------------- #
# probe_ticket_state_from_source / run_state_resolver — dispatch on the kind
# --------------------------------------------------------------------------- #


def test_a_none_source_resolves_to_unknown() -> None:
    assert probe_ticket_state_from_source(None, "T01") is RunState.unknown


def test_a_ticket_absent_from_the_json_resolves_to_absent() -> None:
    # T80: the source resolved and simply does not list this id — RunState.absent,
    # not unknown (which is reserved for "no source to ask" / "could not be read").
    source = RunStateSource(kind="json", path=JSON_FIXTURE)
    assert probe_ticket_state_from_source(source, "T99-absent") is RunState.absent


def test_a_directory_source_delegates_to_the_marker_prober(tmp_path: Path) -> None:
    run_state_dir = tmp_path / ".factory" / "run-state"
    (run_state_dir / "merged").mkdir(parents=True)
    (run_state_dir / "merged" / "CAD-100").write_text("", encoding="utf-8")
    source = RunStateSource(kind="directory", path=run_state_dir)

    assert probe_ticket_state_from_source(source, "CAD-100") is RunState.merged
    # T80: the directory form's "present dir, no marker" default is RunState.absent,
    # not RunState.todo — the directory resolved and does not list this id.
    assert probe_ticket_state_from_source(source, "CAD-999") is RunState.absent


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
