"""Unit tests for the write-path run-state gate (:mod:`write_gate`).

Pins the core v2 safety invariant: :func:`ensure_mutable` RETURNS a ticket's
:class:`RunState` only when it is editable (``todo``/``unknown``) and raises the
canonical :class:`TicketNotMutable` (HTTP 409) for every read-only state — the
directory form's ``in-flight``/``ready``/``merged`` AND the factory JSON's
``in_progress``/``in_part``/``in_submilestone``/``flagged``/``failed``/
``needs_human``, AND (T80) ``absent`` — a resolved source that simply does not
list the ticket, refused precisely BECAUSE ``unknown`` (no source at all) stays
mutable and the two must never be conflated. Exercised against BOTH committed
fixtures (read-only): the ``tests/fixtures/projects/with_run_state`` marker
directory and the factory-shaped ``tests/fixtures/run_state/run-state.json``. A
``PathTraversal`` for an unsafe id must propagate unchanged on the directory
source, and a final guard asserts the gate performs NO filesystem mutation on
the fixture's run-state tree.

T80's amendment adds two more axes, both pinned here: a VACUOUS source — a
run-state directory holding no marker at all, or a ``tickets: {}`` JSON — lists
nobody and so resolves the mutable ``unknown`` for every id (with the
one-entry-only cases asserted alongside as the guard against over-correcting),
and :func:`ensure_deletable` permits ``absent`` where :func:`ensure_mutable`
refuses it, so an ungated ``create`` cannot mint an undeletable ticket.

T80's SECOND amendment adds the axis that fails closed: a source that EXISTS and
cannot be READ resolves ``unreadable``, which BOTH gates refuse — asserted here on
the resolved state (so it is distinguishable from ``absent``) and on the refusal
naming the source path, with the "no source at all stays mutable" and "a vacuous
source stays mutable" cases above standing as its regression guards.

T80's FOURTH amendment widens that axis from "could not be READ" to "the information
is UNAVAILABLE": a source read perfectly well that lists THIS ticket under an entry
this console cannot interpret — a status outside the alias table, a non-string status,
an entry that is not an object — resolves the same refusing ``unreadable``. Pinned here
on all four shapes, on the refusal NAMING the value it could not read (so an operator
is sent to the right fix), on the refusal staying per-entry rather than per-file, and
— as the guard against over-refusing — on the three ways a source says NOTHING (no
source, vacuous, unparseable document) still resolving the mutable ``unknown``.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from factory_console.domain import Project, RunState, RunStateSource
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.write_gate import (
    DELETABLE_STATES,
    MUTABLE_STATES,
    TicketNotMutable,
    ensure_deletable,
    ensure_mutable,
)

# The committed fixture project, located RELATIVE to this test file so the suite
# is path-independent. Its run-state directory is ``<fixture>/.factory/run-state``.
_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "projects" / "with_run_state"
_FIXTURE_RUN_STATE_DIR = _FIXTURE_ROOT / ".factory" / "run-state"

# Fixture ground truth: which ticket id resolves to which run-state. Every id in
# this fixture carries an explicit marker (T80 changed the directory's
# "present dir, no marker" default from todo to absent — refused).
_TODO_IDS = ("CAD-131", "CAD-140", "CAD-152")
_NON_MUTABLE_IDS = {
    "CAD-125": RunState.in_flight,
    "CAD-118": RunState.ready,
    "CAD-100": RunState.merged,
}
# CAD-999 has no marker anywhere under the (present) run-state dir: the T80 case
# this whole ticket exists for — the source resolved and does not list it.
_ABSENT_DIRECTORY_ID = "CAD-999"

# The factory-shaped JSON fixture and its ground truth for the six states only it
# can name — the states an operator most needs the gate to refuse.
_JSON_FIXTURE = Path(__file__).parents[1] / "fixtures" / "run_state" / "run-state.json"
_JSON_IDS = {
    RunState.in_progress: "T56",
    RunState.in_part: "T57",
    RunState.in_submilestone: "T58",
    RunState.flagged: "T74",
    RunState.failed: "T75",
    RunState.needs_human: "T76",
}
# T99-absent has no entry in the JSON fixture's tickets object at all.
_ABSENT_JSON_ID = "T99-absent"


def _make_project(
    *, run_state_dir: Path | None, run_state_source: RunStateSource | None = None
) -> Project:
    """Build a Project rooted at the fixture with the given run-state source."""
    return Project(
        rootPath=_FIXTURE_ROOT,
        ticketsManifestPath=_FIXTURE_ROOT / "docs" / "planning" / "tickets.json",
        ticketsDir=_FIXTURE_ROOT / "docs" / "planning" / "tickets",
        roadmapPath=_FIXTURE_ROOT / "ROADMAP.md",
        runStateDir=run_state_dir,
        runStateSource=run_state_source,
        discoveredAt=datetime(2026, 7, 25, 12, 0, 0),
    )


def _make_json_project() -> Project:
    """A Project whose run-state comes from the factory-shaped JSON fixture."""
    return _make_project(
        run_state_dir=None,
        run_state_source=RunStateSource(kind="json", path=_JSON_FIXTURE),
    )


def _snapshot_run_state_tree() -> dict[str, float]:
    """Map every path under the fixture run-state dir to its mtime (for mutation checks)."""
    return {
        str(path.relative_to(_FIXTURE_RUN_STATE_DIR)): path.stat().st_mtime
        for path in sorted(_FIXTURE_RUN_STATE_DIR.rglob("*"))
    }


# --------------------------------------------------------------------------- #
# ensure_mutable — mutable states return (no raise)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ticket_id", _TODO_IDS)
def test_ensure_mutable_returns_todo_for_todo_ids(ticket_id: str) -> None:
    project = _make_project(run_state_dir=_FIXTURE_RUN_STATE_DIR)
    assert ensure_mutable(project, ticket_id) == RunState.todo, (
        f"a todo id ({ticket_id}) must be mutable and resolve to RunState.todo"
    )


def test_ensure_mutable_returns_unknown_when_no_run_state_dir() -> None:
    # runStateDir=None (no run-state directory on disk) -> RunState.unknown, which
    # is mutable, so the gate returns rather than raising.
    project = _make_project(run_state_dir=None)
    assert ensure_mutable(project, "CAD-131") == RunState.unknown, (
        "a project with no run-state dir must resolve to the mutable RunState.unknown"
    )


# --------------------------------------------------------------------------- #
# ensure_mutable — read-only states raise TicketNotMutable (409)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "ticket_id, expected_state",
    list(_NON_MUTABLE_IDS.items()),
    ids=list(_NON_MUTABLE_IDS),
)
def test_ensure_mutable_raises_for_read_only_states(
    ticket_id: str, expected_state: RunState
) -> None:
    project = _make_project(run_state_dir=_FIXTURE_RUN_STATE_DIR)
    with pytest.raises(TicketNotMutable) as exc_info:
        ensure_mutable(project, ticket_id)

    exc = exc_info.value
    assert exc.code == "ticket_not_mutable"
    assert exc.status == 409
    assert exc.details == {"ticketId": ticket_id, "runState": expected_state.value}
    # The message must name the offending state so the error is self-describing.
    assert expected_state.value in exc.message


def test_ensure_mutable_refuses_a_ticket_the_factory_json_marked_merged() -> None:
    # The regression this whole change exists for: a JSON-sourced project has NO
    # run-state directory, so a gate reading runStateDir would see unknown —
    # mutable — and wave through an edit to a ticket the factory already merged.
    with pytest.raises(TicketNotMutable) as exc_info:
        ensure_mutable(_make_json_project(), "T01")

    assert exc_info.value.details == {"ticketId": "T01", "runState": "merged"}


def test_ensure_mutable_allows_a_ticket_the_factory_json_marked_todo() -> None:
    assert ensure_mutable(_make_json_project(), "T77") == RunState.todo


# --------------------------------------------------------------------------- #
# ensure_mutable — absent (T80): a resolved source that does not list the ticket
# --------------------------------------------------------------------------- #


def test_ensure_mutable_refuses_a_ticket_absent_from_the_directory_source() -> None:
    # The directory is present but names no marker for this id anywhere — the
    # T80 gap: distinct from "no run-state dir at all" (which stays mutable).
    project = _make_project(run_state_dir=_FIXTURE_RUN_STATE_DIR)
    with pytest.raises(TicketNotMutable) as exc_info:
        ensure_mutable(project, _ABSENT_DIRECTORY_ID)

    exc = exc_info.value
    assert exc.code == "ticket_not_mutable"
    assert exc.status == 409
    assert exc.details == {"ticketId": _ABSENT_DIRECTORY_ID, "runState": RunState.absent.value}
    # T80 step 4 mandates naming the consulted source for `absent` — for BOTH source
    # kinds, not just JSON. Without this, threading `source_path` through only the
    # json branch of `ensure_mutable` would still pass the suite.
    assert str(_FIXTURE_RUN_STATE_DIR) in exc.message


def test_ensure_mutable_refuses_a_ticket_absent_from_the_json_source() -> None:
    with pytest.raises(TicketNotMutable) as exc_info:
        ensure_mutable(_make_json_project(), _ABSENT_JSON_ID)

    exc = exc_info.value
    assert exc.details == {"ticketId": _ABSENT_JSON_ID, "runState": RunState.absent.value}
    # The distinct message names the resolved source path — see write_gate.py.
    assert str(_JSON_FIXTURE) in exc.message


def test_ensure_mutable_still_allows_edits_with_no_run_state_source_at_all() -> None:
    # The regression guard: removing the run-state source entirely must still
    # leave every ticket mutable via RunState.unknown, never absent.
    project = _make_project(run_state_dir=None)
    assert ensure_mutable(project, "CAD-999-anything") == RunState.unknown


# --------------------------------------------------------------------------- #
# ensure_mutable — a VACUOUS source (T80 amendment, gap 1) lists nobody -> unknown
# --------------------------------------------------------------------------- #


def _project_with_run_state_dir(run_state_dir: Path) -> Project:
    """A Project whose run-state is the given (tmp) marker directory."""
    return Project(
        rootPath=run_state_dir.parent,
        ticketsManifestPath=run_state_dir.parent / "docs" / "planning" / "tickets.json",
        ticketsDir=run_state_dir.parent / "docs" / "planning" / "tickets",
        runStateDir=run_state_dir,
        discoveredAt=datetime(2026, 8, 1, 12, 0, 0),
    )


def _project_with_run_state_json(path: Path, payload: str) -> Project:
    """A Project whose run-state is a ``run-state.json`` written with ``payload``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return Project(
        rootPath=path.parent.parent,
        ticketsManifestPath=path.parent.parent / "docs" / "planning" / "tickets.json",
        ticketsDir=path.parent.parent / "docs" / "planning" / "tickets",
        runStateSource=RunStateSource(kind="json", path=path),
        discoveredAt=datetime(2026, 8, 1, 12, 0, 0),
    )


def test_ensure_mutable_permits_every_ticket_when_the_directory_lists_nobody(
    tmp_path: Path,
) -> None:
    # An empty-but-valid run-state directory exercises no authority over anybody, so
    # it must resolve the mutable `unknown` — not `absent` for every ticket, which
    # would refuse every write in the project (T80's own "unknown -> mutable must
    # survive" invariant).
    run_state_dir = tmp_path / "project" / ".factory" / "run-state"
    run_state_dir.mkdir(parents=True)
    project = _project_with_run_state_dir(run_state_dir)

    for ticket_id in ("CAD-131", "CAD-999", "T01"):
        assert ensure_mutable(project, ticket_id) == RunState.unknown


def test_ensure_mutable_still_refuses_an_id_a_populated_directory_omits(tmp_path: Path) -> None:
    # The regression guard for the amendment: ONE marker is enough for the directory
    # to speak, and it does not name T02 — still refused, exactly as before.
    run_state_dir = tmp_path / "project" / ".factory" / "run-state"
    (run_state_dir / "todo").mkdir(parents=True)
    (run_state_dir / "todo" / "T01").write_text("", encoding="utf-8")
    project = _project_with_run_state_dir(run_state_dir)

    assert ensure_mutable(project, "T01") == RunState.todo
    with pytest.raises(TicketNotMutable) as exc_info:
        ensure_mutable(project, "T02")
    assert exc_info.value.details == {"ticketId": "T02", "runState": RunState.absent.value}


def test_ensure_mutable_permits_every_ticket_when_the_json_lists_nobody(tmp_path: Path) -> None:
    # The JSON form of the same rule: ``tickets: {}`` parses fine and names nobody.
    project = _project_with_run_state_json(
        tmp_path / "project" / ".factory" / "run-state.json",
        '{"version": 1, "tickets": {}}',
    )

    for ticket_id in ("T01", "CAD-999"):
        assert ensure_mutable(project, ticket_id) == RunState.unknown


def test_ensure_mutable_still_refuses_an_id_a_populated_json_omits(tmp_path: Path) -> None:
    project = _project_with_run_state_json(
        tmp_path / "project" / ".factory" / "run-state.json",
        '{"version": 1, "tickets": {"T01": {"status": "todo", "pr_url": null}}}',
    )

    assert ensure_mutable(project, "T01") == RunState.todo
    with pytest.raises(TicketNotMutable) as exc_info:
        ensure_mutable(project, "T02")
    assert exc_info.value.details == {"ticketId": "T02", "runState": RunState.absent.value}


# --------------------------------------------------------------------------- #
# ensure_deletable — everything ensure_mutable allows, PLUS absent (gap 2)
# --------------------------------------------------------------------------- #


def test_ensure_deletable_permits_a_ticket_absent_from_the_source() -> None:
    # The whole point of the second gate: `absent` is deletable though not editable,
    # so a ticket the (ungated) create path minted can always be removed again.
    project = _make_project(run_state_dir=_FIXTURE_RUN_STATE_DIR)
    assert ensure_deletable(project, _ABSENT_DIRECTORY_ID) == RunState.absent
    assert ensure_deletable(_make_json_project(), _ABSENT_JSON_ID) == RunState.absent


def test_ensure_deletable_still_refuses_every_other_read_only_state() -> None:
    # Widening delete for `absent` must not have widened it for a lane-owned state.
    project = _make_project(run_state_dir=_FIXTURE_RUN_STATE_DIR)
    for ticket_id, state in _NON_MUTABLE_IDS.items():
        with pytest.raises(TicketNotMutable) as exc_info:
            ensure_deletable(project, ticket_id)
        assert exc_info.value.details == {"ticketId": ticket_id, "runState": state.value}


# --------------------------------------------------------------------------- #
# unreadable (T80 amendment 2) — a source that EXISTS and cannot be read is
# refused BOTH writes, distinguishably from absent
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permission bits")
def test_an_unreadable_directory_source_refuses_edit_and_delete(tmp_path: Path) -> None:
    # The regression this amendment exists for: before it, a chmod-000 run-state
    # directory resolved the MUTABLE `unknown`, so every write was granted precisely
    # because the gate could not check. It must now refuse both writes, and the
    # refusal must name the source path — an operator has to know this is a
    # permissions problem on that path, not a missing ticket entry.
    run_state_dir = tmp_path / "project" / ".factory" / "run-state"
    (run_state_dir / "todo").mkdir(parents=True)
    (run_state_dir / "todo" / "T01").write_text("", encoding="utf-8")
    project = _project_with_run_state_dir(run_state_dir)
    run_state_dir.chmod(0o000)
    try:
        with pytest.raises(TicketNotMutable) as edit_exc:
            ensure_mutable(project, "T01")
        with pytest.raises(TicketNotMutable) as delete_exc:
            ensure_deletable(project, "T01")
    finally:
        run_state_dir.chmod(0o755)

    for exc_info in (edit_exc, delete_exc):
        exc = exc_info.value
        assert exc.code == "ticket_not_mutable"
        assert exc.status == 409
        # The resulting STATE is the assertion that distinguishes this from `absent`;
        # the message is only checked for the path it must name.
        assert exc.details == {"ticketId": "T01", "runState": RunState.unreadable.value}
        assert str(run_state_dir) in exc.message


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
def test_an_unreadable_json_source_refuses_edit_and_delete(tmp_path: Path) -> None:
    # The JSON form of the same rule. `T01` is recorded `todo` — editable if the file
    # could be read — so this pins that the refusal comes from the unreadability and
    # not from the ticket's recorded state.
    project = _project_with_run_state_json(
        tmp_path / "project" / ".factory" / "run-state.json",
        '{"version": 1, "tickets": {"T01": {"status": "todo", "pr_url": null}}}',
    )
    json_path = tmp_path / "project" / ".factory" / "run-state.json"
    json_path.chmod(0o000)
    try:
        with pytest.raises(TicketNotMutable) as edit_exc:
            ensure_mutable(project, "T01")
        with pytest.raises(TicketNotMutable) as delete_exc:
            ensure_deletable(project, "T01")
    finally:
        json_path.chmod(0o644)

    for exc_info in (edit_exc, delete_exc):
        assert exc_info.value.details == {
            "ticketId": "T01",
            "runState": RunState.unreadable.value,
        }
        assert str(json_path) in exc_info.value.message


def test_the_unreadable_refusal_reads_differently_from_the_absent_one() -> None:
    # Amendment 2 requires the two refusals to be TOLD APART. The state is what a
    # client switches on and is asserted everywhere else; here — once, in the one
    # place that is about the operator-facing prose — the two messages are asserted
    # to differ, because "could not be read" and "not listed" are different problems
    # with different fixes, and `absent` additionally offers the delete that
    # `unreadable` refuses.
    absent = TicketNotMutable("T01", RunState.absent, source_path=Path("/p/run-state.json"))
    unreadable = TicketNotMutable("T01", RunState.unreadable, source_path=Path("/p/run-state.json"))

    assert absent.message != unreadable.message
    assert absent.details != unreadable.details
    assert str(Path("/p/run-state.json")) in unreadable.message


# --------------------------------------------------------------------------- #
# unclassifiable (T80 amendment 4) — a source that was READ and whose entry for
# THIS ticket could not be INTERPRETED refuses too, and the refusal names the value
# --------------------------------------------------------------------------- #


def test_an_unrecognised_status_refuses_edit_and_delete_and_names_the_value(
    tmp_path: Path,
) -> None:
    # The failure amendment 4 exists to close, end to end at the gate: the factory
    # gains a tenth FAC_STATES member, this console does not know the name, and a
    # ticket a lane is actively reviewing must NOT read as editable. Before the
    # amendment `in_review` resolved the mutable `unknown` and this edit was granted.
    project = _project_with_run_state_json(
        tmp_path / "project" / ".factory" / "run-state.json",
        '{"version": 1, "tickets": {"T01": {"status": "in_review", "pr_url": null}}}',
    )
    json_path = tmp_path / "project" / ".factory" / "run-state.json"

    with pytest.raises(TicketNotMutable) as edit_exc:
        ensure_mutable(project, "T01")
    with pytest.raises(TicketNotMutable) as delete_exc:
        ensure_deletable(project, "T01")

    for exc_info in (edit_exc, delete_exc):
        exc = exc_info.value
        assert exc.status == 409
        assert exc.details == {"ticketId": "T01", "runState": RunState.unreadable.value}
        # Step 1 of the amendment: the refusal NAMES the unrecognised value. An
        # operator who reads "not tracked" goes looking for a missing entry; the entry
        # is right there, and what they actually need is a console that knows the
        # status the factory now writes.
        assert "in_review" in exc.message
        assert str(json_path) in exc.message


def test_an_unrecognised_state_directory_refuses_both_writes_and_names_the_directory(
    tmp_path: Path,
) -> None:
    # T92 at the gate, the DIRECTORY form's version of the test above, and reached with
    # no change to this module: `probe_ticket_state_with_reason` now fills the same
    # `unclassifiable` slot for a marker directory this console has no name for, so
    # `TicketNotMutable`'s existing branch phrases it. The failure it closes is worse
    # than the JSON one: T01 is named ONLY under `in_review/`, so before T92 it resolved
    # `absent` — which is DELETABLE — and the console would have deleted a ticket a lane
    # owns rather than merely edited it.
    run_state_dir = tmp_path / "project" / ".factory" / "run-state"
    (run_state_dir / "in_review").mkdir(parents=True)
    (run_state_dir / "in_review" / "T01").write_text("", encoding="utf-8")
    project = _project_with_run_state_dir(run_state_dir)

    with pytest.raises(TicketNotMutable) as edit_exc:
        ensure_mutable(project, "T01")
    with pytest.raises(TicketNotMutable) as delete_exc:
        ensure_deletable(project, "T01")

    for exc_info in (edit_exc, delete_exc):
        exc = exc_info.value
        assert exc.status == 409
        assert exc.details == {"ticketId": "T01", "runState": RunState.unreadable.value}
        # Criterion 3: the refusal NAMES the directory, in the same words the JSON form
        # names a status. "Not tracked" would send an operator hunting a marker that is
        # right there, and "could not be read" would send them to chmod a directory
        # whose permissions are fine; the fix is a console that knows `in_review`.
        assert "state 'in_review'" in exc.message
        assert "could not be read" not in exc.message
        assert str(run_state_dir) in exc.message


def test_an_unrecognised_state_directory_naming_nobody_leaves_the_project_mutable(
    tmp_path: Path,
) -> None:
    # T92's converse, at the gate rather than at the resolver, because over-refusal is
    # what the per-id rule exists to prevent: a stray `in_review/` that names NOBODY
    # must not turn a project read-only. T01 keeps its `todo` marker and stays editable;
    # T02, which the source does not name, keeps the `absent` it already had.
    run_state_dir = tmp_path / "project" / ".factory" / "run-state"
    (run_state_dir / "todo").mkdir(parents=True)
    (run_state_dir / "todo" / "T01").write_text("", encoding="utf-8")
    (run_state_dir / "in_review").mkdir()
    project = _project_with_run_state_dir(run_state_dir)

    assert ensure_mutable(project, "T01") is RunState.todo
    with pytest.raises(TicketNotMutable) as exc_info:
        ensure_mutable(project, "T02")
    assert exc_info.value.details == {"ticketId": "T02", "runState": RunState.absent.value}
    assert ensure_deletable(project, "T02") is RunState.absent


def test_the_unclassifiable_refusal_does_not_borrow_the_unreadable_permissions_prose() -> None:
    # The two routes to `unreadable` are the same authorization answer and must stay
    # the same STATE — `details` is deliberately identical, so a client switching on
    # `runState` never parses prose. What must differ is the remedy the message hands
    # an operator: one is fixed with chmod, the other by upgrading the console.
    path = Path("/p/run-state.json")
    could_not_read = TicketNotMutable("T01", RunState.unreadable, source_path=path)
    could_not_interpret = TicketNotMutable(
        "T01", RunState.unreadable, source_path=path, unclassifiable="status 'in_review'"
    )

    assert could_not_read.details == could_not_interpret.details
    assert could_not_read.message != could_not_interpret.message
    assert "could not be read" in could_not_read.message
    assert "could not be read" not in could_not_interpret.message
    assert "in_review" in could_not_interpret.message


@pytest.mark.parametrize(
    ("entry", "expected_phrase"),
    [
        pytest.param('{"status": "in_review"}', "in_review", id="unrecognised-status"),
        pytest.param('{"status": 7}', "not a string", id="non-string-status"),
        pytest.param('{"pr_url": null}', "no status", id="no-status"),
        pytest.param('"merged"', "not an object", id="entry-is-not-an-object"),
    ],
)
def test_every_uninterpretable_entry_shape_refuses_both_writes(
    entry: str, expected_phrase: str, tmp_path: Path
) -> None:
    # The amendment's reachability list, each shape pinned at the gate. `{"T42":
    # "merged"}` in particular needs no new factory state at all — it is a schema
    # drift the factory could ship tomorrow, and the console must not guess where the
    # status lives just because a human could read it.
    project = _project_with_run_state_json(
        tmp_path / "project" / ".factory" / "run-state.json",
        f'{{"version": 1, "tickets": {{"T01": {entry}, "T02": {{"status": "todo"}}}}}}',
    )

    for gate in (ensure_mutable, ensure_deletable):
        with pytest.raises(TicketNotMutable) as exc_info:
            gate(project, "T01")
        assert exc_info.value.details["runState"] == RunState.unreadable.value
        assert expected_phrase in exc_info.value.message

    # The refusal is per ENTRY, never per file: one uninterpretable entry must not
    # lock the whole project read-only, or a single schema drift takes the console
    # down for every ticket in it.
    assert ensure_mutable(project, "T02") is RunState.todo


def test_amendment_4_does_not_widen_to_a_source_that_said_nothing(tmp_path: Path) -> None:
    # The regression guard the amendment names explicitly: "no source at all → still
    # unknown, still MUTABLE". `unknown` is now exactly "nothing was said", and these
    # three are the ways a source says nothing — no source, a vacuous one, and a
    # document that resolved into nothing and so named no ticket either.
    assert ensure_mutable(_make_project(run_state_dir=None), "T01") is RunState.unknown

    vacuous = _project_with_run_state_json(
        tmp_path / "vacuous" / ".factory" / "run-state.json",
        '{"version": 1, "tickets": {}}',
    )
    assert ensure_mutable(vacuous, "T01") is RunState.unknown

    unparseable = _project_with_run_state_json(
        tmp_path / "broken" / ".factory" / "run-state.json", "{not json at all"
    )
    assert ensure_mutable(unparseable, "T01") is RunState.unknown


def test_unreadable_is_in_neither_allowlist() -> None:
    # The structural half, so widening either tuple has to be deliberate. Unlike
    # `absent`, `unreadable` is not deletable: a source we could not read proves
    # nothing about whether the factory tracks this ticket.
    assert RunState.unreadable not in MUTABLE_STATES
    assert RunState.unreadable not in DELETABLE_STATES


def test_absent_is_deletable_but_not_mutable_at_the_allowlist_level() -> None:
    # The structural half of the same fact, so a future edit to either tuple has to
    # be deliberate: `absent` is in DELETABLE_STATES and NOT in MUTABLE_STATES.
    assert RunState.absent not in MUTABLE_STATES
    assert RunState.absent in DELETABLE_STATES
    assert all(state in DELETABLE_STATES for state in MUTABLE_STATES)


@pytest.mark.parametrize("state", list(RunState), ids=[s.value for s in RunState])
def test_ensure_deletable_raises_iff_state_is_not_deletable(state: RunState) -> None:
    project, ticket_id = _project_and_id_for(state)

    if state not in DELETABLE_STATES:
        with pytest.raises(TicketNotMutable):
            ensure_deletable(project, ticket_id)
    else:
        assert ensure_deletable(project, ticket_id) == state


# --------------------------------------------------------------------------- #
# Property-style — raises IFF the state is not todo/unknown
# --------------------------------------------------------------------------- #


def _project_and_id_for(state: RunState) -> tuple[Project, str]:
    """A (project, ticket_id) whose prober result is exactly ``state`` in the fixture."""
    if state is RunState.unknown:
        # unknown is only reachable with no run-state dir on disk.
        return _make_project(run_state_dir=None), "CAD-131"
    if state is RunState.unreadable:
        # A source that EXISTS and whose bytes cannot be read. Reached here without
        # chmod — which would skip under root and leave a mode-000 path behind for
        # pytest's tmp_path cleanup — by pointing the JSON form at a DIRECTORY:
        # ``read_text`` raises ``IsADirectoryError``, an OSError that is not "the file
        # is not there", which is exactly the condition amendment 2 refuses.
        return (
            _make_project(
                run_state_dir=None,
                run_state_source=RunStateSource(kind="json", path=_FIXTURE_ROOT / ".factory"),
            ),
            "CAD-131",
        )
    if state is RunState.absent:
        return _make_project(run_state_dir=_FIXTURE_RUN_STATE_DIR), _ABSENT_DIRECTORY_ID
    if state is RunState.todo:
        return _make_project(run_state_dir=_FIXTURE_RUN_STATE_DIR), "CAD-131"
    if state in _JSON_IDS:
        # The six factory-only states are unreachable through the marker
        # directory — only the JSON source can name them.
        return _make_json_project(), _JSON_IDS[state]
    id_for_state = {expected: tid for tid, expected in _NON_MUTABLE_IDS.items()}
    return _make_project(run_state_dir=_FIXTURE_RUN_STATE_DIR), id_for_state[state]


@pytest.mark.parametrize("state", list(RunState), ids=[s.value for s in RunState])
def test_ensure_mutable_raises_iff_state_is_read_only(state: RunState) -> None:
    project, ticket_id = _project_and_id_for(state)
    should_raise = state not in MUTABLE_STATES

    if should_raise:
        with pytest.raises(TicketNotMutable):
            ensure_mutable(project, ticket_id)
    else:
        assert ensure_mutable(project, ticket_id) == state, (
            f"{state!r} is mutable, so ensure_mutable must return it, not raise"
        )


# --------------------------------------------------------------------------- #
# ensure_mutable — unsafe ids: PathTraversal propagates unchanged
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_id", ["../etc", "..", "foo/bar"])
def test_ensure_mutable_propagates_path_traversal(bad_id: str) -> None:
    project = _make_project(run_state_dir=_FIXTURE_RUN_STATE_DIR)
    # The gate does not catch the prober's PathTraversal — it surfaces unchanged so
    # the edge layer maps the uniform invalid_ticket_id (400) contract.
    with pytest.raises(PathTraversal):
        ensure_mutable(project, bad_id)


@pytest.mark.parametrize("bad_id", ["../etc", "..", ".", "foo/bar"])
def test_ensure_mutable_no_run_state_dir_does_not_raise_path_traversal(bad_id: str) -> None:
    # Contract boundary: with no run-state dir on disk the prober short-circuits to
    # the mutable RunState.unknown BEFORE it validates the id, so an unsafe id is
    # NOT rejected here — ensure_mutable returns unknown rather than raising. This
    # gate authorizes by run-state only and does no path I/O; a downstream writer
    # must re-validate the id before using it as a filesystem path segment.
    project = _make_project(run_state_dir=None)
    assert ensure_mutable(project, bad_id) == RunState.unknown, (
        "with runStateDir=None the prober resolves unknown before the id is checked, "
        "so the gate must return unknown, not raise PathTraversal"
    )


# --------------------------------------------------------------------------- #
# GUARD — the gate mutates nothing on the committed fixture's run-state tree
# --------------------------------------------------------------------------- #


def test_ensure_mutable_does_not_mutate_the_run_state_tree() -> None:
    before = _snapshot_run_state_tree()

    project = _make_project(run_state_dir=_FIXTURE_RUN_STATE_DIR)
    for ticket_id in _TODO_IDS:
        ensure_mutable(project, ticket_id)
    for ticket_id in _NON_MUTABLE_IDS:
        with pytest.raises(TicketNotMutable):
            ensure_mutable(project, ticket_id)

    after = _snapshot_run_state_tree()
    assert after == before, (
        "ensure_mutable must not create, delete, or touch anything under the run-state dir"
    )
