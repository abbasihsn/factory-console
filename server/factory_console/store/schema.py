"""The console's only SQLite entry point: how a connection is opened, and how it is migrated.

Every write the console makes to its own store goes through :func:`connect`, so the
per-connection guarantees below hold everywhere by construction rather than by each
caller remembering them. Nothing else in the codebase calls ``sqlite3.connect``.

**One connection per operation.** The registry port (T106) is synchronous, so the
backend calls it from anyio's worker-thread pool — and a cached ``sqlite3.Connection``
shared across those threads trips SQLite's thread affinity. Rather than paper over
that with ``check_same_thread=False`` plus a lock we do not otherwise need,
:func:`connect` opens a fresh connection and closes it again around each operation.
A local-file connect is sub-millisecond and the registry's queries return single-digit
row counts, so this buys the removal of an entire class of concurrency bug for nothing
real.

**Versioning via ``PRAGMA user_version``.** ARCHITECTURE.md chose SQLite over a flat
file specifically so a later audit log / multi-user need lands as a migration instead
of a hand-edit of a user's file — which a bare ``CREATE TABLE IF NOT EXISTS`` with no
version marker would forfeit, leaving v3.1 to guess a live db's shape by introspection.
So the mechanism lands now at the smallest size that is genuinely a mechanism: SQLite's
built-in ``user_version`` integer plus :data:`_MIGRATIONS`, an ordered tuple of
callables. No migrations table (``user_version`` is free, and it commits atomically
with the migration that bumped it), no Alembic, and no new dependency — ``sqlite3`` is
stdlib.

**Refuse a newer db.** If ``user_version`` exceeds the highest migration THIS build
knows, the file was written by a newer console whose extra columns and constraints this
build cannot interpret. Opening it read-write and "migrating forward" would silently
write rows the newer schema may constrain differently, so we fail closed with
:class:`StoreSchemaTooNew`, naming both versions.

Usage — callers wrap each operation in :func:`connect` and call :func:`migrate` once on
the connection they were given::

    with connect(resolve_db_path()) as conn:
        migrate(conn)
        ...

On an up-to-date db ``migrate`` is one pragma read, which is why it can sit on every
operation: first-touch creation stays lazy (a viewer session that never opens the store
creates nothing) and no boot hook has to run migrations ahead of time.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from factory_console.errors import FactoryConsoleError
from factory_console.store.location import ensure_store_dir

# Mode for the db FILE itself, the counterpart to location.py's 0700 on its directory.
# Set at creation — ahead of v3.1 putting a password hash in this file — so v3.1
# inherits a correctly-permissioned store instead of shipping a chmod against files
# already in the wild.
DB_FILE_MODE = 0o600

# How long a connect/statement waits for another connection's write lock before
# raising ``database is locked``. Generous because the contending writers are this
# single-user console's own worker threads: a wait of a few seconds is invisible,
# while a spurious failure is a 500 on a click. ``timeout`` covers the connect,
# ``busy_timeout`` (the same value in ms) the statements on it.
LOCK_TIMEOUT_SECONDS = 5.0


class StoreSchemaTooNew(FactoryConsoleError):
    """The store was written by a newer console than this build can understand.

    Mapped to HTTP 500: from a client's point of view this is the server being
    misconfigured (an older binary pointed at a newer file), not a bad request.
    Deliberately carries **no filesystem path** — where the db lives is the server's
    business, not something to echo back to a browser — only the two version numbers
    an operator needs to see which side to move.
    """

    def __init__(self, found: int, supported: int) -> None:
        super().__init__(
            code="store_schema_too_new",
            message=(
                f"Console store schema version {found} is newer than the supported "
                f"version {supported}; upgrade factory-console to open this store."
            ),
            status=500,
            details={"found": found, "supported": supported},
        )
        self.found = found
        self.supported = supported


def _migration_1(conn: sqlite3.Connection) -> None:
    """Create the v3.0 store: the project registry and the one-row selection."""
    conn.execute(
        # UNIQUE on `path` makes the DATABASE the authority on "a project is
        # registered once", rather than a pre-insert SELECT that application code
        # can forget — or lose a race to. A duplicate add surfaces as
        # sqlite3.IntegrityError, which the registry translates.
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY NOT NULL,
            name TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            added_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        # CHECK (id = 1) makes "there is exactly one selection" a schema fact
        # instead of a convention: a second row cannot be inserted at all, so no
        # reader has to decide which of two selection rows wins.
        #
        # ON DELETE SET NULL is why removing the selected project cannot leave a
        # dangling selection pointing at a row that no longer exists — the delete
        # clears the pointer in the same statement. It requires
        # `PRAGMA foreign_keys = ON`, which connect() sets; without it SQLite
        # silently ignores the clause.
        """
        CREATE TABLE console_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            selected_project_id TEXT NULL REFERENCES projects(id) ON DELETE SET NULL
        )
        """
    )
    # Seed the single row now, so every later read is a plain UPDATE/SELECT against a
    # row that is guaranteed to exist — no upsert, no "no selection row yet" branch.
    conn.execute("INSERT INTO console_state (id, selected_project_id) VALUES (1, NULL)")


# The ordered migration chain: index i (0-based) is migration i + 1, and a db at
# ``user_version = n`` has had the first n of these applied. Migrations are APPEND-only
# and never edited once released — an edited migration would leave two databases in the
# wild claiming the same version with different shapes.
#
# NOTE: migration 2, the `credentials` table, is **v3.1's** work and is NOT written
# here. It arrives as a new `_migration_2` appended to this tuple, which is the whole
# point of the mechanism: SCHEMA_VERSION follows the tuple's length, so an existing
# user's file is upgraded on first open rather than hand-edited.
_MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (_migration_1,)

SCHEMA_VERSION = len(_MIGRATIONS)
"""The schema version this build writes and understands.

Derived from :data:`_MIGRATIONS` rather than maintained alongside it, so adding a
migration cannot leave the constant behind (which would silently skip the new one).
"""


def migrate(conn: sqlite3.Connection) -> int:
    """Bring ``conn``'s database up to :data:`SCHEMA_VERSION` and return its version.

    Idempotent, and cheap when there is nothing to do: an up-to-date db costs one
    ``PRAGMA user_version`` read and no transaction, which is what lets callers call
    this once per connection instead of arranging a boot-time migration hook.

    Each pending migration runs in its own transaction that also carries its
    ``PRAGMA user_version`` bump, so a failure mid-migration rolls the version back
    with the DDL — a db is never left claiming a version whose tables did not commit.
    ``user_version`` cannot be parameterised (SQLite does not accept a placeholder in
    a PRAGMA), so the value is formatted into the statement; it is an ``int`` derived
    from this module's own tuple index and **never** from caller or client input.

    Args:
        conn: An open connection from :func:`connect`.

    Returns:
        The database's schema version afterwards — always :data:`SCHEMA_VERSION`.

    Raises:
        StoreSchemaTooNew: ``user_version`` exceeds :data:`SCHEMA_VERSION`; the
            database is left untouched.
    """
    current: int = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise StoreSchemaTooNew(found=current, supported=SCHEMA_VERSION)
    for index in range(current, SCHEMA_VERSION):
        version = index + 1
        conn.execute("BEGIN")
        try:
            _MIGRATIONS[index](conn)
            # Literal, not a placeholder: PRAGMA takes no parameters. `version` is an
            # int from range() over our own tuple, so this is not an injection seam.
            conn.execute(f"PRAGMA user_version = {version}")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    return SCHEMA_VERSION


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a connection to the console store at ``db_path``, closing it on exit.

    One connection per operation — see the module docstring for why a cached one is
    the wrong shape here. The connection is closed on every path, including an
    exception raised by the body.

    Creates the store directory (via ``location.ensure_store_dir``, the only
    directory-creating function in this package) and, if this call is what brings the
    db file into existence, chmods it to 0600. Whether the file pre-existed is
    recorded *before* ``sqlite3.connect``, since connecting is itself what creates it.

    Per-connection state, none of which survives the close:

    - ``row_factory = sqlite3.Row`` so callers read columns by name.
    - ``PRAGMA foreign_keys = ON`` — **required**: it is off by default per
      connection, and without it ``console_state``'s ``ON DELETE SET NULL`` is
      silently inert and deleting a selected project leaves a dangling selection.
    - ``PRAGMA journal_mode = WAL`` (persistent in the file, set here so a db created
      by any caller gets it) so a reader is not blocked by a writer.
    - ``PRAGMA busy_timeout`` so a contended write waits instead of failing at once.

    ``isolation_level=None`` turns off the driver's implicit transaction handling: this
    module issues ``BEGIN``/``COMMIT`` itself in :func:`migrate`, and DDL under the
    driver's autocommit heuristics would otherwise land outside the transaction meant
    to contain it.

    Args:
        db_path: The db file, as returned by ``location.resolve_db_path``.

    Yields:
        The open connection. Call :func:`migrate` on it once before using it.
    """
    ensure_store_dir(db_path)
    # Before the connect, which creates the file if it is missing.
    was_created = not db_path.exists()
    conn = sqlite3.connect(db_path, timeout=LOCK_TIMEOUT_SECONDS, isolation_level=None)
    try:
        if was_created:
            os.chmod(db_path, DB_FILE_MODE)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(f"PRAGMA busy_timeout = {int(LOCK_TIMEOUT_SECONDS * 1000)}")
        yield conn
    finally:
        conn.close()
