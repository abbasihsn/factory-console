"""Unit tests for the SQLite-backed :class:`SqliteProjectRegistry`.

Two layers, kept apart exactly as ``tests/unit/test_fake_registry.py`` keeps them.
The PORT's behaviour is pinned once in ``tests/_registry_contract.py`` and run from
here, so the real store and the fake are held to ONE behaviour spec and cannot
drift on duplicate detection, canonicalisation, or whether removing the selected
project clears the selection.

What remains here is what is true of the SQLITE one alone, and each case is a
property the fake cannot have:

- **Laziness** — constructing a registry creates no directory and no file. This is
  the property that lets ``create_app`` wire a registry unconditionally, and it is
  invisible to the shared suite, which only ever calls methods.
- **Durability** — a second instance over the same file sees the first one's rows
  and its selection, because nothing is cached in the object.
- **The FK doing the work** — removing the selected project clears
  ``console_state`` through ``ON DELETE SET NULL``, asserted against the raw
  column rather than only through :meth:`get_selected_project`, because the clause
  is silently inert without ``PRAGMA foreign_keys = ON``.
- **Fail-closed on a newer schema** — a db written by a newer console surfaces
  :class:`StoreSchemaTooNew` from the FIRST call, not from construction.

Every case runs against ``tmp_path``, so no test can touch a developer's real
store.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from _registry_contract import assert_registry_conforms  # top-level test helper

from factory_console.store.registry_protocol import ProjectRegistry
from factory_console.store.schema import SCHEMA_VERSION, StoreSchemaTooNew
from factory_console.store.sqlite_registry import SqliteProjectRegistry

_BASE = Path("/factory-console-sqlite-registry")
_ALPHA = _BASE / "alpha"
_BETA = _BASE / "beta"
_ADDED_AT = datetime(2026, 8, 6, 12, 30, 45, tzinfo=UTC)


def _fresh_registry_factory(tmp_path: Path) -> Callable[[], ProjectRegistry]:
    """Return a ``make_registry`` handing out a registry over its OWN new db file.

    The shared contract suite calls its factory afresh for every sub-case and
    expects clean state each time (see its docstring). For an in-memory fake that
    is free; here it means a NEW file per call, since a second registry over one
    file would — correctly — still see the previous sub-case's rows and turn an
    ordering assertion into a leak from whichever case ran before it.
    """
    counter = iter(range(1_000))
    return lambda: SqliteProjectRegistry(tmp_path / f"store-{next(counter)}" / "console.db")


def _ids(*ids: str) -> Callable[[], str]:
    """An ``id_factory`` handing out ``ids`` in order, defaulting to a single id."""
    remaining = iter(ids or ("a" * 32,))
    return lambda: next(remaining)


def _raw_selected_project_id(db_path: Path) -> str | None:
    """Read ``console_state.selected_project_id`` behind the registry's back."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT selected_project_id FROM console_state WHERE id = 1"
        ).fetchone()[0]
    finally:
        conn.close()


class TestPortContract:
    """The shared conformance suite, run against the real store."""

    def test_sqlite_registry_satisfies_the_registry_contract(self, tmp_path: Path) -> None:
        assert_registry_conforms(_fresh_registry_factory(tmp_path))


class TestLazyConstruction:
    """Constructing a registry touches nothing; the first CALL creates everything."""

    def test_construction_creates_no_directory_and_no_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "sub" / "console.db"

        SqliteProjectRegistry(db_path)

        assert not db_path.parent.exists()
        assert not db_path.exists()

    def test_first_call_creates_the_store(self, tmp_path: Path) -> None:
        db_path = tmp_path / "sub" / "console.db"
        registry = SqliteProjectRegistry(db_path)

        assert registry.list_projects() == []

        assert db_path.parent.exists()
        assert db_path.exists()

    def test_default_db_path_is_resolved_but_not_touched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The no-argument form the CLI uses is lazy too — ``resolve_db_path`` is pure."""
        db_path = tmp_path / "home-store" / "console.db"
        monkeypatch.setenv("FACTORY_CONSOLE_DB_PATH", str(db_path))

        registry = SqliteProjectRegistry()

        assert not db_path.parent.exists()
        assert registry.list_projects() == []
        assert db_path.exists()


class TestDurability:
    """The rows live in the file, not in the object."""

    def test_rows_and_selection_survive_a_fresh_instance(self, tmp_path: Path) -> None:
        db_path = tmp_path / "store" / "console.db"
        added = SqliteProjectRegistry(db_path).add_project(_ALPHA, "Alpha")
        SqliteProjectRegistry(db_path).set_selected_project(added.id)

        reopened = SqliteProjectRegistry(db_path)

        assert reopened.list_projects() == [added]
        assert reopened.get_project(added.id) == added
        assert reopened.find_by_path(f"{_BASE}/beta/../alpha") == added
        assert reopened.get_selected_project() == added

    def test_two_instances_over_one_file_read_the_same_rows(self, tmp_path: Path) -> None:
        """Nothing is cached: a write through one instance is visible through the other."""
        db_path = tmp_path / "store" / "console.db"
        first = SqliteProjectRegistry(db_path)
        second = SqliteProjectRegistry(db_path)
        alpha = first.add_project(_ALPHA)

        assert second.list_projects() == [alpha]

        assert second.remove_project(alpha.id) is True
        assert first.list_projects() == []

    def test_added_at_is_stored_as_readable_utc_iso_text(self, tmp_path: Path) -> None:
        """The column an operator may have to inspect by hand, not an epoch int."""
        db_path = tmp_path / "store" / "console.db"
        registry = SqliteProjectRegistry(db_path, clock=lambda: _ADDED_AT)

        added = registry.add_project(_ALPHA)

        conn = sqlite3.connect(db_path)
        try:
            stored = conn.execute("SELECT added_at, path FROM projects").fetchone()
        finally:
            conn.close()
        assert stored[0] == "2026-08-06T12:30:45+00:00"
        assert stored[1] == str(_ALPHA)
        assert added.addedAt == _ADDED_AT

    def test_injected_id_factory_mints_the_stored_id(self, tmp_path: Path) -> None:
        registry = SqliteProjectRegistry(tmp_path / "store" / "console.db", id_factory=_ids())

        assert registry.add_project(_ALPHA).id == "a" * 32
        assert [row.id for row in registry.list_projects()] == ["a" * 32]

    def test_a_colliding_id_factory_is_refused(self, tmp_path: Path) -> None:
        """The primary key's violation is reported as the fake reports it, not raw."""
        registry = SqliteProjectRegistry(
            tmp_path / "store" / "console.db", id_factory=lambda: "a" * 32
        )
        registry.add_project(_ALPHA)

        with pytest.raises(ValueError, match="already registered"):
            registry.add_project(_BETA)


class TestForeignKeyClearsSelection:
    """``ON DELETE SET NULL`` — asserted against the raw column, not only the reader."""

    def test_removing_the_selected_project_nulls_the_stored_selection(self, tmp_path: Path) -> None:
        db_path = tmp_path / "store" / "console.db"
        registry = SqliteProjectRegistry(db_path)
        alpha = registry.add_project(_ALPHA)
        beta = registry.add_project(_BETA)
        registry.set_selected_project(beta.id)
        assert _raw_selected_project_id(db_path) == beta.id

        assert registry.remove_project(beta.id) is True

        # The FK cleared the column itself — no second UPDATE from the registry, and
        # no dangling id left pointing at a row that is gone.
        assert _raw_selected_project_id(db_path) is None
        assert registry.get_selected_project() is None
        # And no fallback to "the only project there is".
        assert registry.list_projects() == [alpha]

    def test_removing_a_non_selected_project_leaves_the_selection(self, tmp_path: Path) -> None:
        db_path = tmp_path / "store" / "console.db"
        registry = SqliteProjectRegistry(db_path)
        alpha = registry.add_project(_ALPHA)
        beta = registry.add_project(_BETA)
        registry.set_selected_project(alpha.id)

        assert registry.remove_project(beta.id) is True

        assert _raw_selected_project_id(db_path) == alpha.id
        assert registry.get_selected_project() == alpha


class TestNewerSchemaIsRefused:
    """A db from a newer console fails closed, from the first call rather than at boot."""

    def test_newer_user_version_surfaces_store_schema_too_new(self, tmp_path: Path) -> None:
        db_path = tmp_path / "store" / "console.db"
        db_path.parent.mkdir()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        finally:
            conn.close()

        registry = SqliteProjectRegistry(db_path)

        with pytest.raises(StoreSchemaTooNew) as excinfo:
            registry.list_projects()

        assert excinfo.value.found == SCHEMA_VERSION + 1
        assert excinfo.value.supported == SCHEMA_VERSION
        # Every entry point goes through the same first-touch seam, so a write
        # refuses for the same reason a read does.
        with pytest.raises(StoreSchemaTooNew):
            registry.add_project(_ALPHA)
