"""SQLite-backed :class:`SqliteProjectRegistry` — the production ProjectRegistry.

The registry-side counterpart of
:class:`~factory_console.file_adapter.real.RealFileAdapter`: where that adapter is
the production implementation of the read seam over a TARGET project's files, this
is the production implementation of
:class:`~factory_console.store.registry_protocol.ProjectRegistry`, the seam over
the console's OWN durable state. It *composes* the small store modules —
:func:`~factory_console.store.location.resolve_db_path`,
:func:`~factory_console.store.schema.connect`,
:func:`~factory_console.store.schema.migrate` and
:func:`~factory_console.store.paths.canonical_project_path` — rather than
re-implementing path canonicalisation, migration or connection policy here, and it
satisfies the port STRUCTURALLY (no inheritance), exactly as the file adapters do:
``isinstance(registry, ProjectRegistry)`` holds because the Protocol is
``@runtime_checkable``. It is the only module in the codebase that runs SQL.

**Construction is side-effect-free, and that is load-bearing.**
``SqliteProjectRegistry(db_path)`` does not connect, does not migrate and does not
create a directory: it stores a path and nothing else
(:func:`~factory_console.store.location.resolve_db_path` is pure). The database
file and its parent directory are created on the first method CALL, inside
:meth:`SqliteProjectRegistry._conn`. That is what makes it safe for ``create_app``
to wire a registry unconditionally — which in turn is what lets the backend use the
RAISING DI provider shape (``get_file_adapter``'s) instead of an opt-in ``None``
registry that every handler would have to invent an answer for. A local viewer
session that opens one project and never touches a registry endpoint leaves no
trace in the user's home directory.

**One connection per operation.** Every method opens, migrates and closes its own
connection through :func:`~factory_console.store.schema.connect`, for the reason
that module documents: the port is synchronous and the backend calls it from
anyio's worker-thread pool (``await anyio.to_thread.run_sync(partial(...))``, per
ARCHITECTURE.md's concurrency rule), and a cached :class:`sqlite3.Connection`
shared across those threads trips SQLite's thread affinity. This class therefore
does **no async work itself** and holds no connection, no cursor and no cached
rows — two instances over one file always read the same state.

**The DATABASE is the authority on duplicates, not this class.** A duplicate path
is detected by catching the ``UNIQUE`` index's :class:`sqlite3.IntegrityError` and
re-raising :class:`~factory_console.store.registry_protocol.DuplicateProjectPath`,
never by a pre-check ``SELECT``: a check-then-insert is a race the schema already
precludes, and it would let two authorities disagree. The same rule holds for the
selection's clearing on delete — that is the schema's ``ON DELETE SET NULL``
(active because :func:`~factory_console.store.schema.connect` sets
``PRAGMA foreign_keys = ON``), not a second ``UPDATE`` issued by hand here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from factory_console.domain.registry import RegisteredProject
from factory_console.store.location import resolve_db_path
from factory_console.store.paths import canonical_project_path, default_project_name
from factory_console.store.registry_protocol import DuplicateProjectPath, ProjectNotRegistered
from factory_console.store.schema import connect, migrate

# The projects columns every read selects, named once so the SELECTs cannot drift
# from the mapper that reads their rows, and spelled out rather than `SELECT *` so
# a later migration's new column cannot silently change what these queries return.
# The joined form is DERIVED from the same tuple rather than written a second time.
_PROJECT_COLUMNS = ("id", "name", "path", "added_at")
_SELECT_COLUMNS = ", ".join(_PROJECT_COLUMNS)
_SELECT_COLUMNS_JOINED = ", ".join(f"p.{column}" for column in _PROJECT_COLUMNS)


class SqliteProjectRegistry:
    """:class:`ProjectRegistry` backed by the console's own SQLite store.

    Holds exactly three pieces of state, all set at construction and none of them
    I/O: the db path, the id seam and the clock seam. Everything else lives in the
    database, so this object is cheap to build, safe to build unconditionally, and
    safe to share between requests.
    """

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Point the registry at a db file WITHOUT touching it.

        ``db_path`` defaults to
        :func:`~factory_console.store.location.resolve_db_path`, which is pure —
        it stats nothing and creates nothing — so this constructor performs no
        I/O at all and the laziness rule in the module docstring holds even for
        the no-argument form the CLI uses.

        ``id_factory`` and ``clock`` are the same determinism seams
        :class:`~factory_console.store.fake_registry.FakeProjectRegistry` takes,
        defaulted to the same values the port documents (``uuid4().hex`` /
        ``datetime.now(UTC)``), so ``tests/_registry_contract.py`` runs against
        both implementations with the same shape of injection and a test can
        assert on an exact id or stamp without patching :mod:`uuid` or freezing
        global time.
        """
        self._db_path = db_path if db_path is not None else resolve_db_path()
        self._id_factory = id_factory if id_factory is not None else lambda: uuid4().hex
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    def add_project(self, path: Path | str, name: str | None = None) -> RegisteredProject:
        """Register ``path`` and return the stored row. See the port for the contract.

        The INSERT is attempted unconditionally and the ``UNIQUE`` index decides:
        a :class:`sqlite3.IntegrityError` is translated into
        :class:`DuplicateProjectPath`, whose ``existingId`` is then read back from
        the row that actually holds the path. Unlike the fake — which can afford
        to check its dict first — a refused add here has already consumed a value
        from a scripted ``id_factory``, and that is the deliberate cost of letting
        the database be the single authority rather than racing a pre-check
        ``SELECT`` against a concurrent writer.

        Raises:
            InvalidProjectPath: ``path`` is blank, relative, or unresolvable.
            DuplicateProjectPath: the canonical path is already registered.
            ValueError: the minted id is already taken — impossible for the
                default ``uuid4().hex`` seam, and a caller bug for an injected
                one, reported exactly as the fake reports it rather than as a raw
                :class:`sqlite3.IntegrityError`.
        """
        canonical = canonical_project_path(path)
        row = RegisteredProject(
            id=self._id_factory(),
            name=default_project_name(canonical) if name is None else name,
            path=canonical,
            addedAt=self._clock(),
        )
        with self._conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO projects (id, name, path, added_at) VALUES (?, ?, ?, ?)",
                    (row.id, row.name, str(row.path), row.addedAt.astimezone(UTC).isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise self._insert_conflict(conn, row) from exc
        return row

    def list_projects(self) -> list[RegisteredProject]:
        """Return every row ordered by ``(added_at, id)``, as the port requires.

        Ordered in SQL rather than in Python so the database does the sort it has
        the rows for. The ``added_at`` strings are fixed-shape UTC ISO-8601 (see
        :meth:`_row_to_project`), so SQLite's text collation orders them exactly as
        the :class:`~datetime.datetime` values they encode would be ordered — which
        is what lets ``ORDER BY`` here mean the same thing as the fake's
        ``sorted(key=(addedAt, id))``.
        """
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM projects ORDER BY added_at, id"
            ).fetchall()
        return [self._row_to_project(row) for row in rows]

    def get_project(self, project_id: str) -> RegisteredProject | None:
        """Return the row with ``project_id``, or ``None`` when there is none."""
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        return None if row is None else self._row_to_project(row)

    def find_by_path(self, path: Path | str) -> RegisteredProject | None:
        """Return the row registered at ``path``, or ``None``.

        Canonicalises first — the same rule the write side applied, so any
        spelling of a registered directory finds it — which means
        :class:`~factory_console.store.paths.InvalidProjectPath` propagates for
        input that has no canonical form to compare against.
        """
        canonical = canonical_project_path(path)
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM projects WHERE path = ?",
                (str(canonical),),
            ).fetchone()
        return None if row is None else self._row_to_project(row)

    def remove_project(self, project_id: str) -> bool:
        """Delete the row with ``project_id``. ``True`` if one was removed.

        The selection is NOT cleared here. ``console_state``'s ``ON DELETE SET
        NULL`` does it inside this same statement, which is why removing the
        selected project cannot leave a dangling selection behind — and why
        ``tests/unit/test_sqlite_registry.py`` asserts it explicitly: the clause
        is silently inert without the ``PRAGMA foreign_keys = ON`` that
        :func:`~factory_console.store.schema.connect` sets.
        """
        with self._conn() as conn:
            deleted = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,)).rowcount
        return deleted > 0

    def get_selected_project(self) -> RegisteredProject | None:
        """Return the selected row, or ``None`` — never a fallback to another row.

        One JOIN from the single ``console_state`` row: a NULL
        ``selected_project_id`` matches nothing and the query returns no row,
        which is reported as ``None``. There is deliberately no second query and
        no "well, there is only one project" branch — see the port's no-fallback
        rule.
        """
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLUMNS_JOINED} "
                "FROM console_state AS s "
                "JOIN projects AS p ON p.id = s.selected_project_id "
                "WHERE s.id = 1"
            ).fetchone()
        return None if row is None else self._row_to_project(row)

    def set_selected_project(self, project_id: str | None) -> RegisteredProject | None:
        """Select ``project_id``, or clear the selection with ``None``.

        The row is SELECTed before the ``UPDATE`` because it is the return value
        the port promises — a caller renders the newly selected project without a
        follow-up :meth:`get_selected_project` — and its absence is what
        distinguishes "no such project" from a successful select. That read is not
        a duplicate-style pre-check standing in for a constraint: the foreign key
        on ``selected_project_id`` remains the authority underneath and refuses an
        id that is not there regardless.

        The connection is autocommit (see :func:`~factory_console.store.schema.connect`),
        so the SELECT and the UPDATE are wrapped in one ``BEGIN IMMEDIATE``/``COMMIT``
        transaction — matching :func:`~factory_console.store.schema.migrate`'s own
        pattern — to close the window a concurrent :meth:`remove_project` could
        otherwise interleave through: without it, a row seen as present here could be
        deleted before the UPDATE, which would then violate the foreign key and raise
        a raw :class:`sqlite3.IntegrityError` instead of this method's own contract.

        Raises:
            ProjectNotRegistered: ``project_id`` names no row. The selection is
                left exactly as it was — the ``UPDATE`` never runs.
        """
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                selected: RegisteredProject | None = None
                if project_id is not None:
                    row = conn.execute(
                        f"SELECT {_SELECT_COLUMNS} FROM projects WHERE id = ?",
                        (project_id,),
                    ).fetchone()
                    if row is None:
                        raise ProjectNotRegistered(project_id)
                    selected = self._row_to_project(row)
                conn.execute(
                    "UPDATE console_state SET selected_project_id = ? WHERE id = 1",
                    (project_id,),
                )
            except BaseException:
                if conn.in_transaction:
                    with suppress(sqlite3.Error):
                        conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        return selected

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Yield a migrated connection — the SINGLE first-touch point of this class.

        Everything the module docstring calls lazy happens here and nowhere else:
        :func:`~factory_console.store.schema.connect` creates the store directory
        and the db file, and :func:`~factory_console.store.schema.migrate` brings
        the file up to the schema this build understands. On an up-to-date db
        ``migrate`` is one ``PRAGMA user_version`` read — no transaction, no write
        lock — which is what lets it sit on every operation instead of behind a
        boot hook that would defeat the laziness this class exists to preserve.

        Raises:
            StoreSchemaTooNew: the file was written by a newer console. Surfaced
                from the first method call, not from construction.
        """
        with connect(self._db_path) as conn:
            migrate(conn)
            yield conn

    @staticmethod
    def _row_to_project(row: sqlite3.Row) -> RegisteredProject:
        """Map a ``projects`` row to the domain model, translating both encodings.

        The two columns that are not plain strings on the way back:

        - ``added_at`` is stored as :meth:`datetime.isoformat` in UTC and parsed
          back with :meth:`datetime.fromisoformat`, which restores the timezone
          the domain model requires. ISO-8601 text is a deliberate choice over an
          epoch integer: this file is one an operator may have to inspect by hand
          under the ``sqlite3`` CLI, and it costs nothing, since the fixed-width
          UTC form also sorts lexicographically in the order it sorts
          chronologically (see :meth:`list_projects`).
        - ``path`` is stored as the CANONICAL absolute string the write side
          produced and is rebuilt as a :class:`~pathlib.Path` verbatim — never
          re-resolved, per ``RegisteredProject.path``'s own rule.

        The column names are the schema's snake_case (``added_at``); the model's
        field is camelCase (``addedAt``). The translation is spelled out here so
        neither side has to bend to the other.
        """
        return RegisteredProject(
            id=row["id"],
            name=row["name"],
            path=Path(row["path"]),
            addedAt=datetime.fromisoformat(row["added_at"]),
        )

    @staticmethod
    def _insert_conflict(
        conn: sqlite3.Connection, row: RegisteredProject
    ) -> DuplicateProjectPath | ValueError:
        """Return the error a refused INSERT of ``row`` means, by asking the db which.

        ``projects`` has two uniqueness constraints, and an ``IntegrityError``
        alone does not say which one fired. The path's ``UNIQUE`` index is the
        expected one, and the row already holding that path is SELECTed here
        because its id is what
        :class:`DuplicateProjectPath` carries as ``existingId`` — the field that
        lets a client offer "switch to it" instead of sending the user hunting
        through the list. When no row holds the path, the conflict was the primary
        key on ``id``, reported as the same :class:`ValueError` the fake raises so
        a caller with an injected ``id_factory`` sees one message for one bug
        whichever implementation it is talking to.
        """
        existing = conn.execute(
            "SELECT id FROM projects WHERE path = ?", (str(row.path),)
        ).fetchone()
        if existing is None:
            return ValueError(f"id {row.id} is already registered")
        return DuplicateProjectPath(row.path, existing["id"])


def open_project_registry_or_warn(echo: Callable[[str], None]) -> SqliteProjectRegistry | None:
    """Construct a :class:`SqliteProjectRegistry`, degrading to ``None`` on failure.

    The one policy behind both composition roots (T25's CLI and
    :func:`~factory_console.app.create_dev_app`): a store the console cannot even
    ADDRESS must not take the local viewer down, so ``ValueError``/``RuntimeError`` —
    the whole failure surface of this side-effect-free constructor (a blank or
    unresolvable ``FACTORY_CONSOLE_DB_PATH``, or a home directory that cannot be
    determined) — is caught and reported through ``echo`` rather than raised, leaving
    the caller to fall back to ``project_registry=None``: PINNED MODE, exactly the
    pre-v3 behaviour, not a degraded app. ``echo`` is the caller's own stderr writer
    (``typer.echo``'s ``err=True`` form, or a bare ``print(file=sys.stderr)``) so this
    helper stays free of both Typer and any particular I/O choice.
    """
    try:
        return SqliteProjectRegistry()
    except (ValueError, RuntimeError) as exc:
        echo(f"warning: could not open the project registry: {exc}")
        return None
