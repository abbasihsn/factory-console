"""The shared behavioural contract EVERY :class:`ProjectRegistry` must satisfy.

One parametrizable suite, run against every implementation of the port — the
in-memory fake today, the SQLite store next — so the two cannot drift. That is
not a hypothetical risk in this repo: ``FakeFileAdapter`` and ``RealFileAdapter``
already diverged once, on run-state for an unknown ticket, precisely because each
was pinned by its OWN tests and neither was pinned by a shared one. The
divergences available to a registry are worse — duplicate detection,
canonicalisation, and whether removing the selected project clears the selection
— because a fake can get each of them "reasonably" wrong and still pass a suite
written alongside it.

So the assertions live HERE, phrased against the port and never against an
implementation's internals: the suite constructs registries only through the
``make_registry`` factory it is given, and reads them only through the seven
public methods. Anything an implementation is free to choose (which ids it mints,
what instant it stamps) is asserted on its SHAPE, not its value; the exact-value
assertions for the fake's injected seams belong in the fake's own test module.

This is a test helper, not a test module — the leading underscore keeps pytest
from collecting it, and it imports as a top-level module via the ``tests`` entry
in ``[tool.pytest.ini_options].pythonpath``, exactly like ``_read_only_guard``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from factory_console.domain.registry import REGISTERED_PROJECT_ID_PATTERN
from factory_console.store.paths import InvalidProjectPath
from factory_console.store.registry_protocol import (
    DuplicateProjectPath,
    ProjectNotRegistered,
    ProjectRegistry,
)

# An absolute root that does not exist on any host, chosen deliberately. Every path
# below hangs off it, so the whole suite doubles as the "a registered path that is
# not on disk round-trips unharmed" case, and ``resolve(strict=False)`` over it is
# pure lexical normalisation — no symlink on the machine running the tests can make
# ``..`` collapse somewhere else and turn a contract assertion into a host quirk.
_BASE = Path("/factory-console-registry-contract")
_ALPHA = _BASE / "alpha"
_BETA = _BASE / "beta"
# The same directory as ``_ALPHA``, spelled through a detour. What duplicate
# detection has to see through.
_ALPHA_DETOUR = f"{_BASE}/beta/../alpha/./"
# An id that is well-formed for the port (32 lowercase hex) but names no row, so a
# rejection can only be about the row being absent — never about the id's shape.
_UNKNOWN_ID = "0" * 32

MakeRegistry = Callable[[], ProjectRegistry]


def assert_registry_conforms(make_registry: MakeRegistry) -> None:
    """Assert the registry built by ``make_registry`` satisfies the port's contract.

    ``make_registry`` is called afresh for every sub-case that needs a clean
    registry, rather than once for the whole suite: state left behind by an
    earlier assertion would otherwise decide a later one, and the order in which
    the cases happen to be written is not part of the contract.
    """
    _assert_satisfies_protocol(make_registry)
    _assert_empty_registry_is_total(make_registry)
    _assert_add_round_trips(make_registry)
    _assert_stored_path_is_canonical(make_registry)
    _assert_duplicate_path_is_refused(make_registry)
    _assert_nested_project_is_accepted(make_registry)
    _assert_relative_path_is_refused(make_registry)
    _assert_lookups_hit_and_miss(make_registry)
    _assert_list_is_stably_ordered(make_registry)
    _assert_remove_is_idempotent(make_registry)
    _assert_selection_round_trips(make_registry)
    _assert_unknown_selection_is_refused(make_registry)
    _assert_selection_clears(make_registry)
    _assert_removing_selected_clears_selection(make_registry)


def _assert_satisfies_protocol(make_registry: MakeRegistry) -> None:
    """The implementation satisfies the ``@runtime_checkable`` port structurally."""
    assert isinstance(make_registry(), ProjectRegistry)


def _assert_empty_registry_is_total(make_registry: MakeRegistry) -> None:
    """An empty registry ANSWERS — it does not raise. The port's totality rule."""
    registry = make_registry()
    assert registry.list_projects() == []
    assert registry.get_selected_project() is None
    assert registry.get_project(_UNKNOWN_ID) is None
    assert registry.find_by_path(_ALPHA) is None
    assert registry.remove_project(_UNKNOWN_ID) is False


def _assert_add_round_trips(make_registry: MakeRegistry) -> None:
    """An added row comes back from ``list_projects``, with a well-formed id and stamp."""
    registry = make_registry()
    added = registry.add_project(_ALPHA)

    assert re.fullmatch(REGISTERED_PROJECT_ID_PATTERN, added.id), (
        f"minted id {added.id!r} does not match REGISTERED_PROJECT_ID_PATTERN"
    )
    assert added.addedAt.tzinfo is not None, "addedAt must be timezone-aware"
    # Defaulted from the path's final component, not left empty or path-shaped.
    assert added.name == "alpha"
    assert registry.list_projects() == [added]
    # Registering does NOT select: that is the caller's separate decision.
    assert registry.get_selected_project() is None


def _assert_stored_path_is_canonical(make_registry: MakeRegistry) -> None:
    """Whatever spelling arrives, the STORED path is the one canonical absolute form."""
    registry = make_registry()
    assert registry.add_project(_ALPHA_DETOUR).path == _ALPHA

    expanded = make_registry().add_project("~/factory-console-registry-contract-home")
    assert expanded.path.is_absolute()
    assert "~" not in expanded.path.parts, f"{expanded.path} still carries an unexpanded ~"
    assert expanded.path.name == "factory-console-registry-contract-home"


def _assert_duplicate_path_is_refused(make_registry: MakeRegistry) -> None:
    """Two spellings of one directory are one project — the second add is a 409.

    The error names the EXISTING row's id, which is what lets a client offer
    "switch to it" instead of sending the user hunting through the list.
    """
    registry = make_registry()
    first = registry.add_project(_ALPHA)

    with pytest.raises(DuplicateProjectPath) as excinfo:
        registry.add_project(_ALPHA_DETOUR)

    assert excinfo.value.details["existingId"] == first.id
    # The refused add left no second row behind.
    assert registry.list_projects() == [first]


def _assert_nested_project_is_accepted(make_registry: MakeRegistry) -> None:
    """A project INSIDE a registered project is allowed — a monorepo may hold two."""
    registry = make_registry()
    outer = registry.add_project(_ALPHA)
    inner = registry.add_project(_ALPHA / "packages" / "inner")

    assert {row.id for row in registry.list_projects()} == {outer.id, inner.id}


def _assert_relative_path_is_refused(make_registry: MakeRegistry) -> None:
    """A relative path has no canonical form here, on either the write or the read side."""
    registry = make_registry()
    with pytest.raises(InvalidProjectPath):
        registry.add_project("dev/alpha")
    with pytest.raises(InvalidProjectPath):
        registry.find_by_path("dev/alpha")


def _assert_lookups_hit_and_miss(make_registry: MakeRegistry) -> None:
    """``get_project``/``find_by_path`` return the row, or ``None`` — never another row."""
    registry = make_registry()
    alpha = registry.add_project(_ALPHA)
    registry.add_project(_BETA)

    assert registry.get_project(alpha.id) == alpha
    assert registry.get_project(_UNKNOWN_ID) is None
    # Found through a spelling that was never the one registered.
    assert registry.find_by_path(_ALPHA_DETOUR) == alpha
    assert registry.find_by_path(_BASE / "never-registered") is None


def _assert_list_is_stably_ordered(make_registry: MakeRegistry) -> None:
    """Rows come back ordered by ``(addedAt, id)``, so two renders cannot swap them.

    Asserted as a PROPERTY of the returned list rather than against a hand-written
    expected order: the timestamps are the implementation's to mint, and two rows
    added in one clock tick are exactly the case the ``id`` tie-break exists for —
    an expected order written here would either duplicate that tie-break or pass
    only on an implementation whose clock happens to be fine-grained.
    """
    registry = make_registry()
    for name in ("alpha", "beta", "gamma", "delta"):
        registry.add_project(_BASE / name)

    rows = registry.list_projects()
    assert len(rows) == 4
    assert rows == sorted(rows, key=lambda row: (row.addedAt, row.id))


def _assert_remove_is_idempotent(make_registry: MakeRegistry) -> None:
    """``remove_project`` is ``True`` once and ``False`` thereafter — a double click is fine."""
    registry = make_registry()
    alpha = registry.add_project(_ALPHA)

    assert registry.remove_project(alpha.id) is True
    assert registry.remove_project(alpha.id) is False
    assert registry.list_projects() == []
    # The path is free again: removal released the identity, it did not tombstone it.
    assert registry.find_by_path(_ALPHA) is None
    assert registry.add_project(_ALPHA).path == _ALPHA


def _assert_selection_round_trips(make_registry: MakeRegistry) -> None:
    """``set_selected_project`` returns the row it selected, and the getter agrees."""
    registry = make_registry()
    registry.add_project(_ALPHA)
    beta = registry.add_project(_BETA)

    assert registry.set_selected_project(beta.id) == beta
    assert registry.get_selected_project() == beta


def _assert_unknown_selection_is_refused(make_registry: MakeRegistry) -> None:
    """Selecting an id that names no row RAISES, and leaves the selection untouched."""
    registry = make_registry()
    alpha = registry.add_project(_ALPHA)
    registry.set_selected_project(alpha.id)

    with pytest.raises(ProjectNotRegistered) as excinfo:
        registry.set_selected_project(_UNKNOWN_ID)

    assert excinfo.value.details["projectId"] == _UNKNOWN_ID
    assert registry.get_selected_project() == alpha


def _assert_selection_clears(make_registry: MakeRegistry) -> None:
    """``set_selected_project(None)`` clears, and reports the cleared state as ``None``."""
    registry = make_registry()
    alpha = registry.add_project(_ALPHA)
    registry.set_selected_project(alpha.id)

    assert registry.set_selected_project(None) is None
    assert registry.get_selected_project() is None
    # Cleared, not fallen back to "the only project there is".
    assert registry.list_projects() == [alpha]


def _assert_removing_selected_clears_selection(make_registry: MakeRegistry) -> None:
    """Removing the SELECTED row clears the selection — the schema's ``ON DELETE SET NULL``.

    The single highest-value agreement point in this suite: an implementation
    that leaves the id dangling reports a selection whose row is gone, and every
    read through it fails afterwards. The surviving row must NOT be selected in
    its place either — that is the no-fallback rule, and it is what makes this
    case distinguishable from a lucky guess.
    """
    registry = make_registry()
    alpha = registry.add_project(_ALPHA)
    beta = registry.add_project(_BETA)
    registry.set_selected_project(beta.id)

    assert registry.remove_project(beta.id) is True
    assert registry.get_selected_project() is None
    assert registry.list_projects() == [alpha]
    # Removing a NON-selected row leaves the selection alone.
    registry.set_selected_project(alpha.id)
    gamma = registry.add_project(_BASE / "gamma")
    assert registry.remove_project(gamma.id) is True
    assert registry.get_selected_project() == alpha
