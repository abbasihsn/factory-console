# [T104] Console store location: FACTORY_CONSOLE_DB_PATH and side-effect-free resolution

milestone: v3.0 · track: store · depends_on: T04 · provides: `server/factory_console/store/` (docstring-only, re-export-free `__init__`) and `store/location.py` — `ConsoleStoreSettings` exposing **`FACTORY_CONSOLE_DB_PATH`**, plus `resolve_db_path()` (PURE — creates nothing) and `ensure_store_dir()` (mkdir 0700).

## Context

Before anything can be stored, the console needs one answer to "where is my db", and it needs that
answer to be overridable, because three callers would otherwise fight over the developer's home
directory: the Playwright e2e suite (which boots a real packaged CLI), the Python test suite, and
the developer's own console. `FACTORY_CONSOLE_DB_PATH` is the seam that keeps them apart, and
**T120 depends on it by name** — without it the test suites write into the developer's real registry.

Resolution is deliberately split from creation. `resolve_db_path()` is a pure function that returns
a `Path` and touches nothing; `ensure_store_dir()` is the only thing that makes a directory. That
split is what lets `factory-console PATH` — the local viewer, which must keep working exactly as it
does today — boot, serve and exit without ever creating `~/.factory-console/` on a machine whose
owner never asked for a registry.

Two decisions worth stating rather than absorbing. **The default is literally
`~/.factory-console/console.db` and there is no `XDG_DATA_HOME` lookup**, despite the convention:
ARCHITECTURE.md names that path as the contract, and adding a second resolution rule would give
"where is my db" two answers for the sake of a convention this project already settled — one env var
overriding the whole path is simpler and strictly more capable. **The directory is created 0700 and
the db file will be 0600 at creation**; that is not decoration, because v3.1 puts a password hash in
this exact file, and establishing the mode at creation means v3.1 inherits a correctly-permissioned
file rather than shipping a chmod against files already in the wild.

## Staged approach

1. CREATE `server/factory_console/store/__init__.py` as a DOCSTRING-ONLY module: state what the
   package is (the console's own writable state — the sibling of `file_adapter/`'s read-only
   target-project access) and instruct that submodules are imported by full path
   (`from factory_console.store.location import resolve_db_path`). **Do NOT add `from .x import ...`
   or `__all__`.** This is load-bearing: later tickets add sibling modules to this package in
   parallel-eligible PRs, and a re-exporting `__init__` would be a shared aggregation file every one
   of them had to edit and therefore conflict on.
2. CREATE `server/factory_console/store/location.py`.
3. Define `DEFAULT_STORE_DIRNAME = ".factory-console"` and `DEFAULT_DB_FILENAME = "console.db"`,
   with a module docstring citing ARCHITECTURE.md as the source of the default path and recording
   the rejected XDG alternative in one sentence.
4. Define `class ConsoleStoreSettings(BaseSettings)` with
   `model_config = SettingsConfigDict(env_prefix="FACTORY_CONSOLE_")` and one field
   `db_path: Path | None = None`. Follow `config.py`'s `WriteTokenSettings` shape exactly — a NARROW
   settings class that does not re-validate host/port/log_level, for the same reason
   `read_write_token()` exists: an unrelated non-loopback `FACTORY_CONSOLE_HOST` in the environment
   must not abort a caller that only wanted the db path. Add a `field_validator` rejecting a
   blank/whitespace-only value (an empty override is a mistake, not "use the default").
5. Define `resolve_db_path() -> Path`: read `ConsoleStoreSettings().db_path`; if set, return
   `expanduser().resolve(strict=False)`; else `Path.home() / DEFAULT_STORE_DIRNAME /
   DEFAULT_DB_FILENAME`, resolved the same way. Docstring: **PURE** — it stats nothing and creates
   nothing, so it is safe at import/boot time and safe in a viewer session that will never open the
   db. The override names the **file**, not a directory, so two parallel test runs can point at two
   files in one tmpdir.
6. Define `ensure_store_dir(db_path: Path) -> Path`: `db_path.parent.mkdir(parents=True,
   exist_ok=True)`, then `os.chmod(parent, 0o700)` unconditionally so a pre-existing loose directory
   is tightened; return the parent. Docstring: the ONLY directory-creating function in the store,
   called from the real registry's first-touch path (not from its constructor) — and note that the
   0600 mode of the db FILE is `schema.py`'s business, at the point it creates the file.
7. Modify `docs/usage.md`: a short "Console store" entry documenting `FACTORY_CONSOLE_DB_PATH`, the
   default location, that the file is created lazily on first registry use, and that the local
   `factory-console PATH` viewer never creates it.
8. CREATE `tests/unit/test_store_location.py`: default path with a monkeypatched `Path.home`;
   override via monkeypatched `FACTORY_CONSOLE_DB_PATH` including a `~`-prefixed value; blank
   override rejected; **`resolve_db_path()` creates NOTHING** (assert the parent does not exist
   afterwards under a monkeypatched `HOME` pointing at `tmp_path` — the regression test for the
   viewer-must-not-create-a-registry rule); `ensure_store_dir` creates the tree at 0700, tightens a
   pre-existing 0755, and is idempotent.

## Critical files

- `server/factory_console/store/__init__.py` (create — docstring only, no re-exports)
- `server/factory_console/store/location.py` (create)
- `docs/usage.md` (modify)
- `tests/unit/test_store_location.py` (create)

## Interface & data

`ConsoleStoreSettings(BaseSettings)` with `db_path: Path | None = None` (env
`FACTORY_CONSOLE_DB_PATH`); `resolve_db_path() -> Path` (pure, no filesystem effect);
`ensure_store_dir(db_path: Path) -> Path` (creates the parent 0700, returns it).

Referenced, not redefined: `config.py::WriteTokenSettings` (the narrow-settings-class idiom and the
`FACTORY_CONSOLE_` prefix), ARCHITECTURE.md v3 "Console-owned store" (the
`~/.factory-console/console.db` default).

DB ops: none — this ticket resolves and prepares a location, it does not open sqlite. NFR flags:
file permissions 0700 on the store directory (0600 on the db file is set at creation in T105, ahead
of v3.1's credentials); no new dependency; no relaxation of the loopback boundary.

Cross-track: **T120** sets `FACTORY_CONSOLE_DB_PATH` to a per-run temp file in the e2e harness and
the pytest fixtures; the CLI (T119) must not read it at boot.

## Verification

`python -m pytest tests/unit/test_store_location.py -q`;
`python -m pytest -q --cov=factory_console` (85% gate); `make lint`.
Prove the viewer is untouched: with `HOME` pointed at an empty tmpdir, run
`python -m pytest tests/integration/test_cli.py -q` and confirm no `.factory-console` directory
appears in it.
