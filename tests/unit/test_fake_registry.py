"""Unit tests for the in-memory :class:`FakeProjectRegistry`.

Two layers, deliberately kept apart. The PORT's behaviour is pinned once, in
``tests/_registry_contract.py``, and run from here — every assertion that must
also hold for the SQLite implementation lives there so the two can never drift
(see that module's docstring). What remains here is what is true of the FAKE
alone: its two determinism seams, its constructor's seeding rules, and the
no-shared-mutable-state property that makes it safe to hand one registry to a
test that mutates what it gets back.

Deterministic and I/O-free — pydantic + stdlib only, over paths that exist on no
host.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from _read_only_guard import assert_module_is_read_only  # top-level test helper
from _registry_contract import assert_registry_conforms  # top-level test helper

from factory_console.domain.registry import RegisteredProject
from factory_console.store import fake_registry as fake_registry_module
from factory_console.store.fake_registry import FakeProjectRegistry
from factory_console.store.registry_protocol import DuplicateProjectPath, ProjectNotRegistered

_BASE = Path("/factory-console-fake-registry")
_ALPHA = _BASE / "alpha"
_BETA = _BASE / "beta"
_ADDED_AT = datetime(2026, 8, 6, 12, 30, 45, tzinfo=UTC)


def _row(project_id: str, path: Path, *, name: str | None = None) -> RegisteredProject:
    """Build a seed row directly, bypassing ``add_project``'s id/clock seams."""
    return RegisteredProject(id=project_id, name=name or path.name, path=path, addedAt=_ADDED_AT)


def _scripted_ids(*ids: str) -> Callable[[], str]:
    """An ``id_factory`` handing out ``ids`` in order, then failing loudly."""
    remaining = iter(ids)
    return lambda: next(remaining)


class TestPortContract:
    """The shared conformance suite, run against the fake."""

    def test_fake_satisfies_the_registry_contract(self) -> None:
        assert_registry_conforms(FakeProjectRegistry)

    def test_fake_module_performs_no_filesystem_mutation(self) -> None:
        """The property the fake exists for: it cannot write, create, or delete."""
        assert_module_is_read_only(fake_registry_module)


class TestInjectedSeams:
    """``id_factory`` and ``clock`` — why a test can assert on exact values."""

    def test_injected_id_factory_mints_the_returned_id(self) -> None:
        registry = FakeProjectRegistry(id_factory=_scripted_ids("a" * 32, "b" * 32))

        assert registry.add_project(_ALPHA).id == "a" * 32
        assert registry.add_project(_BETA).id == "b" * 32
        assert [row.id for row in registry.list_projects()] == ["a" * 32, "b" * 32]

    def test_injected_clock_stamps_the_exact_added_at(self) -> None:
        registry = FakeProjectRegistry(clock=lambda: _ADDED_AT)

        added = registry.add_project(_ALPHA)

        assert added.addedAt == _ADDED_AT
        assert registry.get_project(added.id) is not None

    def test_refused_duplicate_does_not_consume_an_id(self) -> None:
        """The duplicate check runs BEFORE the mint, so a scripted factory stays in sync."""
        registry = FakeProjectRegistry(id_factory=_scripted_ids("a" * 32, "b" * 32))
        registry.add_project(_ALPHA)

        with pytest.raises(DuplicateProjectPath):
            registry.add_project(f"{_BASE}/beta/../alpha")

        assert registry.add_project(_BETA).id == "b" * 32

    def test_defaults_mint_a_uuid_hex_and_an_aware_utc_stamp(self) -> None:
        """With no seams injected, the fake produces what the port documents."""
        before = datetime.now(UTC)

        added = FakeProjectRegistry().add_project(_ALPHA)

        assert added.id != FakeProjectRegistry().add_project(_ALPHA).id
        assert added.addedAt.tzinfo is not None
        assert before <= added.addedAt <= datetime.now(UTC)


class TestSeeding:
    """The constructor's rows and selection — refused where the real store would refuse."""

    def test_seeded_rows_are_listed_and_findable_by_any_spelling(self) -> None:
        registry = FakeProjectRegistry([_row("a" * 32, _ALPHA), _row("b" * 32, _BETA)])

        assert [row.id for row in registry.list_projects()] == ["a" * 32, "b" * 32]
        assert registry.find_by_path(f"{_BASE}/beta/../alpha") == _row("a" * 32, _ALPHA)

    def test_seeded_selection_is_reported(self) -> None:
        registry = FakeProjectRegistry([_row("a" * 32, _ALPHA)], selected_id="a" * 32)

        assert registry.get_selected_project() == _row("a" * 32, _ALPHA)

    def test_seeded_selection_of_an_unregistered_id_is_refused(self) -> None:
        with pytest.raises(ProjectNotRegistered):
            FakeProjectRegistry([_row("a" * 32, _ALPHA)], selected_id="c" * 32)

    def test_seeded_duplicate_path_is_refused(self) -> None:
        """Two seeded rows for one directory is the state the ``UNIQUE`` index forbids."""
        with pytest.raises(DuplicateProjectPath):
            FakeProjectRegistry(
                [_row("a" * 32, _ALPHA), _row("b" * 32, _BETA / ".." / "alpha", name="alpha")]
            )

    def test_seeded_duplicate_id_is_refused(self) -> None:
        """Silently clobbering would drop a row the caller believes it seeded."""
        with pytest.raises(ValueError, match="already registered"):
            FakeProjectRegistry([_row("a" * 32, _ALPHA), _row("a" * 32, _BETA)])

    def test_colliding_id_factory_is_refused(self) -> None:
        registry = FakeProjectRegistry(id_factory=lambda: "a" * 32)
        registry.add_project(_ALPHA)

        with pytest.raises(ValueError, match="already registered"):
            registry.add_project(_BETA)


class TestNoSharedMutableState:
    """Nothing the caller holds is the registry's state, in either direction."""

    def test_mutating_a_returned_list_does_not_affect_the_registry(self) -> None:
        registry = FakeProjectRegistry([_row("a" * 32, _ALPHA)])

        listed = registry.list_projects()
        listed.append(_row("b" * 32, _BETA))
        listed.clear()

        assert [row.id for row in registry.list_projects()] == ["a" * 32]

    def test_mutating_the_seed_list_after_construction_does_not_affect_the_registry(
        self,
    ) -> None:
        seed = [_row("a" * 32, _ALPHA)]
        registry = FakeProjectRegistry(seed)

        seed.append(_row("b" * 32, _BETA))
        seed.clear()

        assert [row.id for row in registry.list_projects()] == ["a" * 32]
        assert registry.get_project("b" * 32) is None
