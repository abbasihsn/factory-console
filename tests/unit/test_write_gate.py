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
"""

from __future__ import annotations

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
