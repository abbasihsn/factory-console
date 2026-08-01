"""Unit tests for the read-only factory run-state prober.

Exercises :func:`find_run_state_dir` (fallback probe order) and
:func:`probe_ticket_state` (marker precedence, the ``unknown``/``absent``
defaults, and the defense-in-depth path-traversal guard), building run-state
trees on the fly under ``tmp_path``. A final GUARD test parses this module's
target source and asserts the read-only invariant: it contains no
filesystem-mutating call.
"""

from pathlib import Path

import pytest
from _read_only_guard import (
    assert_module_carries_read_only_header,
    assert_module_is_read_only,
)

from factory_console.domain import RunState
from factory_console.file_adapter import run_state as run_state_module
from factory_console.file_adapter.run_state import (
    PathTraversal,
    find_run_state_dir,
    is_run_state_marker,
    probe_ticket_state,
)

# Each on-disk state directory name paired with the enum member it must map to
# (``in-flight`` -> RunState.in_flight — mapped by value, not string guessing).
_STATE_TO_ENUM = [
    ("todo", RunState.todo),
    ("in-flight", RunState.in_flight),
    ("ready", RunState.ready),
    ("merged", RunState.merged),
]


def _place_marker(run_state_dir: Path, state: str, ticket_id: str, *, as_dir: bool) -> Path:
    """Create ``<run_state_dir>/<state>/<ticket_id>`` as a file or a directory."""
    state_dir = run_state_dir / state
    state_dir.mkdir(parents=True, exist_ok=True)
    marker = state_dir / ticket_id
    if as_dir:
        marker.mkdir()
    else:
        marker.write_text("")
    return marker


# --------------------------------------------------------------------------- #
# probe_ticket_state — unknown / absent defaults
# --------------------------------------------------------------------------- #


def test_no_run_state_dir_resolves_to_unknown() -> None:
    # Absence of the run-state directory -> RunState.unknown (per ARCHITECTURE).
    assert probe_ticket_state(None, "CAD-118") == RunState.unknown, (
        "a missing run-state dir must resolve to RunState.unknown"
    )


def test_present_dir_without_marker_resolves_to_absent(tmp_path: Path) -> None:
    run_state_dir = tmp_path / "run-state"
    run_state_dir.mkdir()
    assert probe_ticket_state(run_state_dir, "CAD-118") == RunState.absent, (
        "a present run-state dir with no marker for the id must resolve RunState.absent "
        "(the directory resolved and does not list this ticket)"
    )


# --------------------------------------------------------------------------- #
# probe_ticket_state — a marker as a FILE or a DIR maps to the right enum
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("as_dir", [False, True], ids=["file-marker", "dir-marker"])
@pytest.mark.parametrize(
    "state, expected",
    _STATE_TO_ENUM,
    ids=[state for state, _ in _STATE_TO_ENUM],
)
def test_marker_maps_to_enum(tmp_path: Path, state: str, expected: RunState, as_dir: bool) -> None:
    run_state_dir = tmp_path / "run-state"
    run_state_dir.mkdir()
    _place_marker(run_state_dir, state, "CAD-118", as_dir=as_dir)
    kind = "dir" if as_dir else "file"
    assert probe_ticket_state(run_state_dir, "CAD-118") == expected, (
        f"a '{state}' marker present as a {kind} must resolve to {expected!r}"
    )


# --------------------------------------------------------------------------- #
# probe_ticket_state — marker precedence (highest state wins)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "present, expected",
    [
        (("merged", "ready"), RunState.merged),
        (("ready", "in-flight"), RunState.ready),
        (("in-flight", "todo"), RunState.in_flight),
    ],
    ids=["merged-beats-ready", "ready-beats-in-flight", "in-flight-beats-todo"],
)
def test_marker_precedence(tmp_path: Path, present: tuple[str, str], expected: RunState) -> None:
    run_state_dir = tmp_path / "run-state"
    run_state_dir.mkdir()
    for state in present:
        _place_marker(run_state_dir, state, "CAD-118", as_dir=False)
    assert probe_ticket_state(run_state_dir, "CAD-118") == expected, (
        f"with markers {present} present, the highest-precedence state {expected!r} must win"
    )


# --------------------------------------------------------------------------- #
# probe_ticket_state — path-traversal ids are refused before any FS lookup
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_id",
    [
        pytest.param("..", id="dotdot"),
        pytest.param(".", id="dot"),
        pytest.param("foo/bar", id="slash-segment"),
        pytest.param("", id="empty"),
        pytest.param("a/../b", id="embedded-traversal"),
        pytest.param("CAD-118\n", id="trailing-newline"),
    ],
)
def test_traversal_ticket_id_is_refused(tmp_path: Path, bad_id: str) -> None:
    # Pass a real, existing run-state dir so a raised PathTraversal proves it is
    # the id validation firing, not a missing directory.
    run_state_dir = tmp_path / "run-state"
    run_state_dir.mkdir()
    with pytest.raises(PathTraversal):
        probe_ticket_state(run_state_dir, bad_id)


def test_path_traversal_uses_the_uniform_invalid_ticket_id_contract() -> None:
    # run_state and ticket_md must raise the SAME PathTraversal with the uniform
    # ``invalid_ticket_id`` code (per ARCHITECTURE.md), not two divergent classes.
    from factory_console.file_adapter.ticket_md import PathTraversal as TicketMdPathTraversal

    exc = PathTraversal("../etc/passwd")
    assert exc.code == "invalid_ticket_id"
    assert exc.status == 400
    assert PathTraversal is TicketMdPathTraversal


# --------------------------------------------------------------------------- #
# find_run_state_dir — fallback probe order
# --------------------------------------------------------------------------- #


def test_find_run_state_dir_uses_docs_planning_fallback(tmp_path: Path) -> None:
    fallback = tmp_path / "docs" / "planning" / ".run-state"
    fallback.mkdir(parents=True)
    assert find_run_state_dir(tmp_path) == fallback, (
        "with only docs/planning/.run-state present it must be returned"
    )


def test_find_run_state_dir_prefers_factory_when_both_present(tmp_path: Path) -> None:
    primary = tmp_path / ".factory" / "run-state"
    primary.mkdir(parents=True)
    (tmp_path / "docs" / "planning" / ".run-state").mkdir(parents=True)
    assert find_run_state_dir(tmp_path) == primary, (
        "when both locations exist, .factory/run-state must win the fallback order"
    )


def test_find_run_state_dir_returns_none_when_absent(tmp_path: Path) -> None:
    assert find_run_state_dir(tmp_path) is None, (
        "with neither location present, find_run_state_dir must return None"
    )


def test_find_run_state_dir_ignores_a_non_directory_at_primary(tmp_path: Path) -> None:
    # A plain file at the primary path is not a usable run-state dir (is_dir, not
    # exists), so the probe must fall through to the docs/planning location.
    factory = tmp_path / ".factory"
    factory.mkdir()
    (factory / "run-state").write_text("")
    fallback = tmp_path / "docs" / "planning" / ".run-state"
    fallback.mkdir(parents=True)
    assert find_run_state_dir(tmp_path) == fallback, (
        "a non-directory at the primary path must be skipped in favor of the fallback"
    )


# --------------------------------------------------------------------------- #
# is_run_state_marker — the marker-layout rule (shared with the T40 watcher)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "rel_path, expected",
    [
        # A marker lives exactly <location>/<state>/<ticket_id> — two segments
        # below either documented run-state location.
        (".factory/run-state/ready/T99", True),
        (".factory/run-state/in-flight/T42", True),
        ("docs/planning/.run-state/ready/T88", True),  # the fallback location
        # Not markers: the bare location (depth 0), a bare <state> dir (depth 1),
        # and something deeper than a marker (depth 3+).
        (".factory/run-state", False),
        (".factory/run-state/ready", False),
        (".factory/run-state/ready/T99/extra", False),
        # Outside any run-state location entirely (planning docs).
        ("docs/planning/tickets/T99.md", False),
        ("README.md", False),
    ],
)
def test_is_run_state_marker_only_true_at_marker_depth(rel_path: str, expected: bool) -> None:
    assert is_run_state_marker(rel_path) is expected


# --------------------------------------------------------------------------- #
# GUARD: the read-only invariant — the module has no FS-mutating call
# (shared with tests/integration/test_real_file_watcher.py via
# tests/_read_only_guard.py)
# --------------------------------------------------------------------------- #


def test_module_source_has_no_filesystem_mutation() -> None:
    assert_module_is_read_only(run_state_module)


def test_module_source_carries_the_read_only_header() -> None:
    assert_module_carries_read_only_header(run_state_module)
