"""Unit tests for the read-only factory run-state prober.

Exercises :func:`find_run_state_dir` (fallback probe order) and
:func:`probe_ticket_state` (marker precedence, the ``unknown``/``todo`` defaults,
and the defense-in-depth path-traversal guard), building run-state trees on the
fly under ``tmp_path``. A final GUARD test parses this module's target source and
asserts the read-only invariant: it contains no filesystem-mutating call.
"""

import ast
import inspect
from pathlib import Path

import pytest

from factory_console.domain import RunState
from factory_console.file_adapter import run_state as run_state_module
from factory_console.file_adapter.run_state import (
    PathTraversal,
    find_run_state_dir,
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
# probe_ticket_state — unknown / todo defaults
# --------------------------------------------------------------------------- #


def test_no_run_state_dir_resolves_to_unknown() -> None:
    # Absence of the run-state directory -> RunState.unknown (per ARCHITECTURE).
    assert probe_ticket_state(None, "CAD-118") == RunState.unknown, (
        "a missing run-state dir must resolve to RunState.unknown"
    )


def test_present_dir_without_marker_resolves_to_todo(tmp_path: Path) -> None:
    run_state_dir = tmp_path / "run-state"
    run_state_dir.mkdir()
    assert probe_ticket_state(run_state_dir, "CAD-118") == RunState.todo, (
        "a present run-state dir with no marker for the id must default to RunState.todo"
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
# GUARD: the read-only invariant — the module has no FS-mutating call
# --------------------------------------------------------------------------- #

# Attribute-call names that mutate the filesystem (Path or os/shutil methods).
_FORBIDDEN_ATTR_CALLS = frozenset(
    {
        "write_text",
        "write_bytes",
        "touch",
        "mkdir",
        "rmdir",
        "unlink",
        "rename",
        "replace",
        "makedirs",
        "remove",
    }
)
# open() mode characters that request writing/creation/truncation.
_FORBIDDEN_OPEN_MODE_CHARS = frozenset("wax+")

_READ_ONLY_HEADER = "# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests."


def _module_source() -> str:
    """Return the on-disk source text of the run_state module under test."""
    source_file = inspect.getsourcefile(run_state_module)
    assert source_file is not None, "could not locate run_state.py source on disk"
    return Path(source_file).read_text()


def _open_mode_arg(call: ast.Call) -> ast.expr | None:
    """Return the ``mode`` argument node of an ``open(...)`` call, if given."""
    if len(call.args) >= 2:
        return call.args[1]
    for keyword in call.keywords:
        if keyword.arg == "mode":
            return keyword.value
    return None


def test_module_source_has_no_filesystem_mutation() -> None:
    tree = ast.parse(_module_source())
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_ATTR_CALLS:
            violations.append(f"{func.attr}() at line {node.lineno}")
        elif isinstance(func, ast.Name) and func.id == "open":
            mode = _open_mode_arg(node)
            if (
                isinstance(mode, ast.Constant)
                and isinstance(mode.value, str)
                and set(mode.value) & _FORBIDDEN_OPEN_MODE_CHARS
            ):
                violations.append(f"open(mode={mode.value!r}) at line {node.lineno}")
    assert not violations, (
        "run_state.py must be read-only but contains mutation calls: " + ", ".join(violations)
    )


def test_module_source_carries_the_read_only_header() -> None:
    assert _READ_ONLY_HEADER in _module_source(), (
        "run_state.py must carry the literal READ-ONLY header comment"
    )
