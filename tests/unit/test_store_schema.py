"""Unit tests for :mod:`factory_console.store.schema`.

Three contracts are pinned here. The first is that a fresh db arrives fully formed:
one connect + migrate reaches ``SCHEMA_VERSION`` with both tables and the single
``console_state`` row present, and doing it again changes nothing — that idempotence is
what allows :func:`~factory_console.store.schema.migrate` to sit on every operation
instead of behind a boot hook.

The second is the fail-closed rule, and it is the one with teeth: a db whose
``user_version`` is higher than this build's must be REFUSED and left byte-for-byte
alone, because a newer console's extra constraints are exactly what this build cannot
know about.

The third is the set of guarantees that are silently inert when missed —
``PRAGMA foreign_keys`` being ON (without it ``ON DELETE SET NULL`` does nothing at
all), the 0600 file inside its 0700 directory that v3.1's credentials inherit, the
UNIQUE index being the authority on duplicate paths, and the connection actually being
closed on the way out.

Every case runs against ``tmp_path``, so no test can touch a developer's real store.
"""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest

from factory_console.store import schema
from factory_console.store.location import STORE_DIR_MODE
from factory_console.store.schema import (
    DB_FILE_MODE,
    SCHEMA_VERSION,
    StoreSchemaTooNew,
    connect,
    migrate,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A db file inside its own directory, which ``ensure_store_dir`` requires."""
    return tmp_path / "store" / "console.db"


def _mode_of(path: Path) -> int:
    """Return just the permission bits of ``path``, without the file-type bits."""
    return stat.S_IMODE(path.stat().st_mode)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    """Return the names of the db's user tables, straight from ``sqlite_master``."""
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def _user_version(db_path: Path) -> int:
    """Read ``user_version`` over a raw connection, bypassing this module's helpers."""
    raw = sqlite3.connect(db_path)
    try:
        return int(raw.execute("PRAGMA user_version").fetchone()[0])
    finally:
        raw.close()


def test_fresh_db_migrates_to_the_current_version_with_both_tables(db_path: Path) -> None:
    with connect(db_path) as conn:
        assert migrate(conn) == SCHEMA_VERSION
        assert _table_names(conn) >= {"projects", "console_state"}

    assert _user_version(db_path) == SCHEMA_VERSION


def test_migrate_is_idempotent_across_connects(db_path: Path) -> None:
    # The property that lets every operation call migrate(): a second (and third) pass
    # over an up-to-date db must be a no-op, not a re-run of migration 1 — which would
    # fail on CREATE TABLE, or duplicate the seeded selection row.
    with connect(db_path) as conn:
        migrate(conn)
    with connect(db_path) as conn:
        assert migrate(conn) == SCHEMA_VERSION
    with connect(db_path) as conn:
        assert migrate(conn) == SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) AS n FROM console_state").fetchone()["n"] == 1


def test_console_state_holds_exactly_one_row(db_path: Path) -> None:
    with connect(db_path) as conn:
        migrate(conn)

        row = conn.execute("SELECT id, selected_project_id FROM console_state").fetchone()
        assert row["id"] == 1
        assert row["selected_project_id"] is None

        # CHECK (id = 1) is what makes "exactly one selection" a schema fact rather
        # than a convention application code has to uphold.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO console_state (id, selected_project_id) VALUES (2, NULL)")


def test_db_file_is_0600_inside_a_0700_directory(db_path: Path) -> None:
    # v3.1 puts a password hash in this file; the modes are established at creation so
    # that release inherits a tight store instead of shipping a chmod for files already
    # in the wild.
    with connect(db_path) as conn:
        migrate(conn)

    assert _mode_of(db_path) == DB_FILE_MODE
    assert _mode_of(db_path.parent) == STORE_DIR_MODE


def test_foreign_keys_are_on_inside_the_context(db_path: Path) -> None:
    # Not decoration: foreign_keys is OFF by default per connection, and with it off
    # console_state's ON DELETE SET NULL is silently ignored, so deleting the selected
    # project would leave the selection pointing at a row that no longer exists.
    with connect(db_path) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

        migrate(conn)
        conn.execute(
            "INSERT INTO projects (id, name, path, added_at) VALUES (?, ?, ?, ?)",
            ("p1", "One", "/tmp/one", "2026-01-01T00:00:00Z"),
        )
        conn.execute("UPDATE console_state SET selected_project_id = 'p1' WHERE id = 1")

        conn.execute("DELETE FROM projects WHERE id = 'p1'")

        selected = conn.execute("SELECT selected_project_id FROM console_state").fetchone()
        assert selected["selected_project_id"] is None


def test_duplicate_project_path_is_rejected_by_the_unique_index(db_path: Path) -> None:
    # The UNIQUE index — not a pre-insert SELECT in application code — is the authority
    # on "a project is registered once".
    with connect(db_path) as conn:
        migrate(conn)
        conn.execute(
            "INSERT INTO projects (id, name, path, added_at) VALUES (?, ?, ?, ?)",
            ("p1", "One", "/projects/one", "2026-01-01T00:00:00Z"),
        )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO projects (id, name, path, added_at) VALUES (?, ?, ?, ?)",
                ("p2", "One again", "/projects/one", "2026-01-02T00:00:00Z"),
            )


def test_a_newer_schema_is_refused_and_left_unmodified(db_path: Path) -> None:
    with connect(db_path) as conn:
        migrate(conn)
    # A db from a hypothetical newer console: one version ahead, with a table this
    # build knows nothing about.
    raw = sqlite3.connect(db_path)
    try:
        raw.execute("CREATE TABLE credentials (id INTEGER PRIMARY KEY)")
        raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        raw.commit()
        before = {
            row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        raw.close()

    with connect(db_path) as conn:
        with pytest.raises(StoreSchemaTooNew) as excinfo:
            migrate(conn)

        # Fails closed, having changed nothing: same version, same tables, same row.
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION + 1
        assert _table_names(conn) == before
        assert conn.execute("SELECT COUNT(*) AS n FROM console_state").fetchone()["n"] == 1

    error = excinfo.value
    assert error.code == "store_schema_too_new"
    assert error.status == 500
    assert error.details == {"found": SCHEMA_VERSION + 1, "supported": SCHEMA_VERSION}
    # The db's location is the server's business, never a client's.
    assert str(db_path) not in error.message
    assert str(SCHEMA_VERSION + 1) in error.message
    assert str(SCHEMA_VERSION) in error.message
    assert _user_version(db_path) == SCHEMA_VERSION + 1


def test_a_failing_migration_rolls_back_its_version_bump(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    # The reason the version bump shares the migration's transaction: a db must never
    # be left claiming a version whose tables did not commit, or the next open would
    # skip the migration that never actually ran.
    def _half_applied(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE half (id INTEGER PRIMARY KEY)")
        raise RuntimeError("migration blew up")

    monkeypatch.setattr(schema, "_MIGRATIONS", (_half_applied,))
    monkeypatch.setattr(schema, "SCHEMA_VERSION", 1)

    with connect(db_path) as conn:
        with pytest.raises(RuntimeError, match="migration blew up"):
            migrate(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert "half" not in _table_names(conn)


def test_connection_is_closed_after_the_context_exits(db_path: Path) -> None:
    with connect(db_path) as conn:
        migrate(conn)
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_connection_is_closed_when_the_body_raises(db_path: Path) -> None:
    # The close lives in a finally, so a failing operation cannot leak a handle.
    with pytest.raises(RuntimeError):  # noqa: SIM117 - the nested `with` IS the subject
        with connect(db_path) as conn:
            migrate(conn)
            raise RuntimeError("boom")

    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
