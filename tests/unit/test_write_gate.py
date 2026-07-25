"""Unit tests for the write-path run-state gate (:mod:`write_gate`).

Pins the core v2 safety invariant: :func:`ensure_mutable` RETURNS a ticket's
:class:`RunState` only when it is editable (``todo``/``unknown``) and raises the
canonical :class:`TicketNotMutable` (HTTP 409) for the read-only states
(``in-flight``/``ready``/``merged``). Exercised against the committed
``tests/fixtures/projects/with_run_state`` fixture (read-only): a ``PathTraversal``
for an unsafe id must propagate unchanged, and a final guard asserts the gate
performs NO filesystem mutation on the fixture's run-state tree.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from factory_console.domain import Project, RunState
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.write_gate import (
    MUTABLE_STATES,
    TicketNotMutable,
    ensure_mutable,
)

# The committed fixture project, located RELATIVE to this test file so the suite
# is path-independent. Its run-state directory is ``<fixture>/.factory/run-state``.
_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "projects" / "with_run_state"
_FIXTURE_RUN_STATE_DIR = _FIXTURE_ROOT / ".factory" / "run-state"

# Fixture ground truth: which ticket id resolves to which run-state. ``CAD-999``
# is absent from every marker dir, so a present-but-unmarked id defaults to todo.
_TODO_IDS = ("CAD-131", "CAD-140", "CAD-999-absent")
_NON_MUTABLE_IDS = {
    "CAD-125": RunState.in_flight,
    "CAD-118": RunState.ready,
    "CAD-100": RunState.merged,
}


def _make_project(*, run_state_dir: Path | None) -> Project:
    """Build a Project rooted at the fixture with the given run-state directory."""
    return Project(
        rootPath=_FIXTURE_ROOT,
        ticketsManifestPath=_FIXTURE_ROOT / "docs" / "planning" / "tickets.json",
        ticketsDir=_FIXTURE_ROOT / "docs" / "planning" / "tickets",
        roadmapPath=_FIXTURE_ROOT / "ROADMAP.md",
        runStateDir=run_state_dir,
        discoveredAt=datetime(2026, 7, 25, 12, 0, 0),
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


# --------------------------------------------------------------------------- #
# Property-style — raises IFF the state is one of in-flight/ready/merged
# --------------------------------------------------------------------------- #


def _project_and_id_for(state: RunState) -> tuple[Project, str]:
    """A (project, ticket_id) whose prober result is exactly ``state`` in the fixture."""
    if state is RunState.unknown:
        # unknown is only reachable with no run-state dir on disk.
        return _make_project(run_state_dir=None), "CAD-131"
    if state is RunState.todo:
        return _make_project(run_state_dir=_FIXTURE_RUN_STATE_DIR), "CAD-131"
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
