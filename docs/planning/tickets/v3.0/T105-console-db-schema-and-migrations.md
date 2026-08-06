# [T105] Console DB schema + user_version migration runner

milestone: v3.0 · track: store · depends_on: T104 · provides: `store/schema.py` — `connect()` (fresh per-operation connection, `foreign_keys=ON`, WAL, db file 0600) and `migrate()` over `PRAGMA user_version`; migration 1 creates `projects` (with a UNIQUE path index) and the one-row `console_state` carrying the persisted selection.

## Context

This is the ticket that decides whether v3.1 can add the `credentials` table without hand-editing a
user's file. ARCHITECTURE.md's justification for choosing SQLite over a flat file is explicitly "so
a later audit log / multi-user need no migration" — a v3.0 that shipped a bare
`CREATE TABLE IF NOT EXISTS` with no version marker would forfeit exactly that property, and v3.1
would have to guess a live db's shape by introspection. So the migration mechanism lands now, at the
smallest size that is genuinely a mechanism: SQLite's built-in `user_version` pragma plus an ordered
tuple of migration callables. No migration table (`user_version` is free and atomic with the
transaction), no Alembic, **no new dependency** — `sqlite3` is stdlib, so `pyproject.toml` is
untouched.

**The refuse-a-newer-db rule** is MONOTONICITY applied to our own store: if `user_version` exceeds
the highest migration this build knows, the db was written by a newer console and this build cannot
know what its extra columns mean. Opening it read-write and "migrating forward" would silently write
rows a newer schema may constrain differently. It fails closed with a named error naming both
versions.

**Connection handling** is the other decision. The registry port is synchronous (T106), so the
backend calls it from anyio's worker-thread pool — where a cached `sqlite3.Connection` would trip
thread affinity. Rather than manage that with `check_same_thread=False` plus a lock, `connect()`
opens a fresh connection per operation and closes it. A local-file connect is sub-millisecond and
the registry's queries return single-digit row counts, so this trades nothing real for removing an
entire class of concurrency bug.

## Staged approach

1. CREATE `server/factory_console/store/schema.py`. Module docstring: this is the console's only
   sqlite entry point; state the per-operation-connection rationale (anyio worker threads), the
   `user_version` choice with its rationale from ARCHITECTURE.md, and the refuse-a-newer-db rule.
2. Define `class StoreSchemaTooNew(FactoryConsoleError)` here — the module that raises it, per
   ARCHITECTURE.md Cross-cutting, exactly as `ProjectNotFound` lives in `discovery.py`.
   `code="store_schema_too_new"`, status 500, message naming found and supported versions,
   `details={"found": n, "supported": m}`. It carries no filesystem path (the db location is the
   server's business, not a client's).
3. Define `SCHEMA_VERSION` derived from `len(_MIGRATIONS)` so the two cannot drift.
4. Define `_migration_1(conn)`:
   - `CREATE TABLE projects (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, path TEXT NOT NULL
     UNIQUE, added_at TEXT NOT NULL)`
   - `CREATE TABLE console_state (id INTEGER PRIMARY KEY CHECK (id = 1), selected_project_id TEXT
     NULL REFERENCES projects(id) ON DELETE SET NULL)`
   - `INSERT INTO console_state (id, selected_project_id) VALUES (1, NULL)`
   Comment each choice: UNIQUE on `path` makes the DATABASE the authority on duplicates rather than
   a check application code can forget; `CHECK(id = 1)` makes "there is exactly one selection" a
   schema fact; `ON DELETE SET NULL` is why removing the selected project cannot leave a dangling
   selection. Note in a comment that migration 2 (credentials) is **v3.1's** and appends to the
   tuple — it is NOT written here.
5. Define `_MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (_migration_1,)`.
6. Define `migrate(conn) -> int`: read `PRAGMA user_version`; if greater than `SCHEMA_VERSION` raise
   `StoreSchemaTooNew`; else run each pending migration inside a transaction, setting
   `PRAGMA user_version = <n>` in the same transaction (docstring notes `user_version` cannot be
   parameterised, so the value is an int-formatted literal derived from an index, never from input);
   return the final version. Idempotent — a call on an up-to-date db does nothing.
7. Define `@contextmanager def connect(db_path: Path) -> Iterator[sqlite3.Connection]`: call
   `ensure_store_dir(db_path)`; record whether the file pre-existed;
   `sqlite3.connect(db_path, timeout=..., isolation_level=None)`; if newly created,
   `os.chmod(db_path, 0o600)` (ahead of v3.1's credentials); set `row_factory = sqlite3.Row`,
   `PRAGMA foreign_keys = ON` (**required, or `ON DELETE SET NULL` is silently inert**),
   `PRAGMA journal_mode = WAL`, `PRAGMA busy_timeout`; yield; close in a `finally`.
8. Document that callers use `connect()` and call `migrate()` once per connection (a cheap pragma
   read when already current), which keeps first-touch creation lazy and needs no boot hook.
9. CREATE `tests/unit/test_store_schema.py`: a fresh db reaches `SCHEMA_VERSION` and has both
   tables; migrate is idempotent across repeated connects; the db file is 0600 and its directory
   0700; `PRAGMA foreign_keys` is ON inside the context; a db whose `user_version` is manually set to
   `SCHEMA_VERSION + 1` raises `StoreSchemaTooNew` and is NOT modified; `console_state` holds exactly
   one row; a duplicate `path` insert raises `sqlite3.IntegrityError` (pinning that the UNIQUE index,
   not application code, is the authority); the connection is closed after the context exits.

## Critical files

- `server/factory_console/store/schema.py` (create)
- `tests/unit/test_store_schema.py` (create)

## Interface & data

`connect(db_path: Path) -> Iterator[sqlite3.Connection]` (contextmanager);
`migrate(conn: sqlite3.Connection) -> int`; `SCHEMA_VERSION: int`;
`StoreSchemaTooNew(FactoryConsoleError)` — code `store_schema_too_new`, status 500,
details `{found, supported}`.

DB ops: migration 1 creates `projects(id TEXT PK, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
added_at TEXT NOT NULL)` and `console_state(id INTEGER PK CHECK(id=1), selected_project_id TEXT NULL
REFERENCES projects(id) ON DELETE SET NULL)` seeded with one NULL row; version in
`PRAGMA user_version`; per-connection pragmas `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout`.

Referenced, not redefined: `errors.py::FactoryConsoleError` + `to_error_response` (the error
envelope), ARCHITECTURE.md Cross-cutting "concrete subclasses live in the modules that raise them",
`store/location.py::ensure_store_dir`.

NFR flags: db file 0600 at creation (v3.1 credentials); **no new dependency — `pyproject.toml` MUST
NOT change**; blocking I/O, so callers are offloaded by the backend with anyio per the house rule;
fail-closed on an unknown-newer schema.

## Verification

`python -m pytest tests/unit/test_store_schema.py -q`;
`python -m pytest -q --cov=factory_console`; `make lint`.
Manual: open a db under a tmp path, run `migrate`, list `sqlite_master`, then `ls -l` the directory
to confirm 0700 on the dir and 0600 on the file.
