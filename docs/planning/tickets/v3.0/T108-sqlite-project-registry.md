# [T108] SqliteProjectRegistry — the real, lazily-opened console-DB registry

milestone: v3.0 · track: store · depends_on: T105, T107, T17 · provides: `store/sqlite_registry.py` — the production `ProjectRegistry` backed by the console DB, whose construction is SIDE-EFFECT-FREE (the db file and its directory are created on the first method CALL), passing the shared port-contract suite unchanged alongside the fake.

## Context

The real implementation, and the last piece the backend needs to serve registry endpoints. It is the
mirror of `RealFileAdapter` for the console's own store: it composes `store.schema.connect` +
`migrate` and maps rows to `RegisteredProject`, and it is the only module in the codebase that runs
SQL.

**One property is load-bearing and easy to lose: laziness.** `SqliteProjectRegistry(db_path)` must
not connect, must not migrate, and must not create a directory — all of that happens inside each
method, on first use. That is what makes it safe for `create_app` to wire a registry
unconditionally, which in turn is what lets the backend use the raising DI provider shape
(`get_file_adapter`'s) instead of an opt-in `None`. A viewer session that opens one project and never
touches a registry endpoint leaves no trace in the user's home directory, and the honest-degradation
hole a `None` registry would open — an endpoint having to invent an answer from a fact about the
console's own wiring — never exists.

**The second property is that the DATABASE enforces identity, not this class**: a duplicate path is
detected by catching `sqlite3.IntegrityError` on the UNIQUE index and re-raising
`DuplicateProjectPath`, rather than by a pre-check SELECT. A check-then-insert would be a race the
schema already precludes, and it would let the two authorities disagree.

## Staged approach

1. CREATE `server/factory_console/store/sqlite_registry.py`. Module docstring: the real
   `ProjectRegistry`; the lazy-construction rule and why (the local viewer must not create a
   registry); per-operation connections (anyio worker threads); the database-is-the-duplicate-
   authority rule; that it is called from a worker thread by the backend and therefore does no async
   work itself.
2. `class SqliteProjectRegistry` with `__init__(self, db_path: Path | None = None, *,
   id_factory=None, clock=None)`: store `db_path or resolve_db_path()` (`resolve_db_path` is pure,
   so this stays side-effect-free); default the id/clock seams as the fake does so the shared
   contract suite runs against both with the same determinism.
3. Private `@contextmanager def _conn(self)`: `with connect(self._db_path) as conn: migrate(conn);
   yield conn`. This is the single first-touch point that creates the directory and file. Document
   that `migrate` on an up-to-date db is one pragma read.
4. Private `_row_to_project(row: sqlite3.Row) -> RegisteredProject`: parse `added_at` ISO-8601 back
   to a timezone-aware datetime and `path` back to a `Path`. Store `added_at` as
   `datetime.isoformat()` in UTC so the column is human-readable under the `sqlite3` CLI — a
   deliberate choice over an epoch int, since this file is something an operator may have to inspect
   by hand.
5. Implement the seven methods, each a single small SQL statement:
   - `add_project` — canonicalize → INSERT, catching `sqlite3.IntegrityError` and re-raising
     `DuplicateProjectPath` after SELECTing the existing id for `details.existingId`;
   - `list_projects` — `ORDER BY added_at, id`;
   - `get_project`;
   - `find_by_path` — canonicalize then SELECT by path;
   - `remove_project` — DELETE → bool from `rowcount`, **relying on the schema's `ON DELETE SET
     NULL` to clear a selection rather than doing it by hand** — and assert that in the tests,
     because it only works because `connect()` sets `PRAGMA foreign_keys = ON`;
   - `get_selected_project` — one JOIN from `console_state`, returning None when NULL, **with no
     fallback**;
   - `set_selected_project` — verify the id exists → `ProjectNotRegistered`, else
     `UPDATE console_state SET selected_project_id`.
6. Keep the module inside the simple-PR budget: if it runs past ~300 non-test lines, split the
   selection pair (`get_selected_project`/`set_selected_project`) into a follow-up rather than
   compressing docstrings.
7. CREATE `tests/unit/test_sqlite_registry.py`: run `tests/_registry_contract.py` against a
   `tmp_path`-backed instance so the fake and the real one are held to ONE behaviour spec; then
   real-only tests — **construction creates nothing on disk** (assert the parent directory does not
   exist after `SqliteProjectRegistry(tmp_path/"sub"/"console.db")`, and does exist after the first
   `list_projects()`); state survives a fresh instance over the same file (the persisted-selection
   property); removing the selected project clears `console_state` via the FK; two instances over one
   file read the same rows; a db pre-set to a newer `user_version` surfaces `StoreSchemaTooNew` from
   the first call.
8. Do NOT touch `store/__init__.py`; import by full path.

## Critical files

- `server/factory_console/store/sqlite_registry.py` (create)
- `tests/unit/test_sqlite_registry.py` (create)

## Interface & data

`SqliteProjectRegistry(db_path: Path | None = None, *, id_factory=None, clock=None)` implementing
every `ProjectRegistry` method with identical signatures and raise conditions; **construction
performs NO I/O**.

DB operations — `projects`: `INSERT INTO projects (id, name, path, added_at) VALUES (?,?,?,?)`
(IntegrityError on the UNIQUE path index → `DuplicateProjectPath`), `SELECT ... ORDER BY added_at,
id`, `SELECT ... WHERE id = ?`, `SELECT ... WHERE path = ?`, `DELETE FROM projects WHERE id = ?`.
`console_state`: `SELECT selected_project_id FROM console_state WHERE id = 1` (joined to `projects`),
`UPDATE console_state SET selected_project_id = ? WHERE id = 1`. Selection clearing on delete is the
schema's `ON DELETE SET NULL`, which requires `PRAGMA foreign_keys = ON` from `connect()`.

Referenced, not redefined: `store/registry_protocol.py` (the port + its errors),
`store/schema.py::connect/migrate/StoreSchemaTooNew`, `store/paths.py::canonical_project_path`,
`store/location.py::resolve_db_path`, `domain/registry.py::RegisteredProject`,
`file_adapter/real.py` (the real-implementation conventions).

NFR flags: blocking I/O — the backend MUST call every method through `anyio.to_thread.run_sync`
(T98); idempotent migration on every connection; no caching; no auth (loopback boundary unchanged).

## Verification

`python -m pytest tests/unit/test_sqlite_registry.py -q`;
`python -m pytest -q --cov=factory_console` (85% gate); `make lint`.
Prove laziness end to end with `HOME` pointed at an empty tmpdir: construct a
`SqliteProjectRegistry()` and confirm no `.factory-console` directory exists; repeat with a trailing
`.list_projects()` and confirm it does. Regression: `python -m pytest tests/integration -q` and
`scripts/smoke.sh`.
