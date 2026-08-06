# [T106] ProjectRegistry port: the synchronous Protocol, the canonical path rule, and its named errors

milestone: v3.0 · track: store · depends_on: T103, T10, T88 · provides: `store/registry_protocol.py` — the `@runtime_checkable`, SYNCHRONOUS seven-method `ProjectRegistry` Protocol the backend depends on, with `DuplicateProjectPath` / `ProjectNotRegistered` at their raise site — plus `store/paths.py`, the canonical-path rule and `default_project_name()`.

## Context

This is the contract two other tracks are already planning against, so it lands before either
implementation. It follows the exact three-file port shape this repo has used twice
(`protocol.py`+`fake.py`+`real.py`; `writer_protocol.py`+`fake_writer.py`+`real_writer.py`): a
`@runtime_checkable` Protocol the handlers depend on via `Depends()`, a fake for tests, a real one
that owns the backing store.

**The port is SYNCHRONOUS.** Every read port in this codebase is, deliberately, and the backend
offloads it at the handler boundary with `await anyio.to_thread.run_sync(partial(...))` per
ARCHITECTURE.md's Cross-cutting house rule — a `sqlite3` call is blocking I/O for that rule exactly
as a file read is. Making this the one async port would fork the DI and testing shape for queries
that return single-digit row counts.

**The canonical path rule ships with the port** rather than with an implementation, because it IS a
clause of the contract: the port promises that `RegisteredProject.path` is always canonical, so a
consumer never re-resolves it and two implementations cannot disagree about whether two spellings
are the same project. Canonical = `expanduser()` then `resolve(strict=False)`, stored absolute.
`resolve()` is what makes `~/dev/foo`, `/Users/me/dev/foo` and a symlinked alias ONE row, which is
what makes the UNIQUE index meaningful. **`strict=False` is required, not incidental**: the store
must be able to hold — and later read back — a row whose path has since been deleted or unmounted,
and a strict resolve would raise at exactly the moment the condition vocabulary exists to answer
honestly. A RELATIVE path is refused rather than resolved, because it would silently resolve against
a server cwd the caller cannot see. **A project nested inside another registered project is
explicitly ALLOWED** — a monorepo can hold two factory projects and the console has no basis for
refusing the second; stated so it reads as a decision rather than an omission.

The selection methods are part of the port, not a separate seam, because "which project am I looking
at" is registry state with a foreign key into registry rows; splitting it would give the backend two
ports to wire for one table's worth of state.

## Staged approach

1. CREATE `server/factory_console/store/paths.py`. Define
   `class InvalidProjectPath(FactoryConsoleError)` (code `invalid_project_path`, status 400, details
   carrying the offending path AS GIVEN — it is the caller's own input, so echoing it discloses
   nothing they do not already have). Define `canonical_project_path(raw: Path | str) -> Path`:
   reject blank; `expanduser()`; reject a still-relative path with `InvalidProjectPath` naming the
   rule; return `resolve(strict=False)`. Define `default_project_name(path: Path) -> str`: the
   canonical path's final component, falling back to the string form when it has none (a filesystem
   root). Docstring the whole identity rule — symlinks, relative, nesting-allowed, why
   `strict=False`.
2. CREATE `server/factory_console/store/registry_protocol.py`. Module docstring in the style of
   `writer_protocol.py`: what the seam is, that it is synchronous and why, that the DATABASE's UNIQUE
   index (not this Protocol's prose) is the authority on duplicates, and that the port is TOTAL for
   reads — an empty registry is `[]`, an unknown id is `None`, never an exception.
3. Define `class DuplicateProjectPath(FactoryConsoleError)` (code `duplicate_project_path`, 409,
   details `{path, existingId}`) and `class ProjectNotRegistered(FactoryConsoleError)` (code
   `project_not_registered`, 404, details `{projectId}`) in this module — their raise site is any
   conforming implementation, so the port is their home, mirroring `PathTraversal` in
   `path_safety.py`.
4. Define `@runtime_checkable class ProjectRegistry(Protocol)`, each method fully docstringed:
   - `add_project(path: Path | str, name: str | None = None) -> RegisteredProject` — canonicalizes,
     mints a uuid4-hex id matching `REGISTERED_PROJECT_ID_PATTERN`, defaults the name from the path,
     stamps `addedAt` timezone-aware UTC. Raises `InvalidProjectPath` / `DuplicateProjectPath`.
     Explicitly does NOT validate that the path is a factory project and does NOT auto-select:
     registering a path whose volume is currently unmounted must succeed and later read back as a
     NAMED condition, and conflating registration with selection would hide a policy decision inside
     a write.
   - `list_projects() -> list[RegisteredProject]` — every row, ordered by `addedAt` then `id` for a
     stable UI order. Never raises for an empty registry.
   - `get_project(project_id: str) -> RegisteredProject | None`.
   - `find_by_path(path: Path | str) -> RegisteredProject | None` — canonicalizes first, so the
     caller's spelling does not matter; how the backend answers "is this already registered" without
     provoking a 409.
   - `remove_project(project_id: str) -> bool` — True when a row was removed, False when the id was
     unknown (so the edge layer can 404 without a raise/catch round trip). Documents that removing
     the SELECTED project clears the selection (the schema's `ON DELETE SET NULL`) rather than
     leaving a dangling id.
   - `get_selected_project() -> RegisteredProject | None` — the persisted selection, or None.
     **Documents the no-fallback rule: an implementation MUST NOT substitute "the first project"
     when nothing is selected**, because that renders one project's board under the heading the user
     last set to another.
   - `set_selected_project(project_id: str | None) -> RegisteredProject | None` — selects, or clears
     with None; raises `ProjectNotRegistered` for an unknown id.
5. Add a closing docstring paragraph for the backend: wire this like `get_file_adapter` (RAISE when
   unbound — a wiring bug), not like the opt-in `get_file_watcher`, because the port is total and
   there is nothing a `None` registry could honestly mean; a viewer session that never calls it costs
   nothing since the real implementation is lazy (T108).
6. Import submodules by FULL PATH throughout; do NOT add re-exports to `store/__init__.py`.
7. CREATE `tests/unit/test_store_paths.py`: `~` expansion; symlink and non-symlink spellings
   canonicalize equal; a relative path raises `InvalidProjectPath`; a non-existent path canonicalizes
   fine (the `strict=False` regression); trailing slash and `..` normalise; `default_project_name`
   for a normal dir and for `/`.
8. CREATE `tests/unit/test_registry_protocol.py`: the error classes carry the documented
   code/status/details and render through `to_error_response`; the Protocol is `runtime_checkable`
   and a stub with the seven methods satisfies `isinstance`.

## Critical files

- `server/factory_console/store/paths.py` (create)
- `server/factory_console/store/registry_protocol.py` (create)
- `tests/unit/test_store_paths.py` (create)
- `tests/unit/test_registry_protocol.py` (create)

## Interface & data

Port (NEW), `@runtime_checkable`, SYNCHRONOUS — the seven methods above.
Errors: `DuplicateProjectPath` (409, `{path, existingId}`), `ProjectNotRegistered` (404,
`{projectId}`), `InvalidProjectPath` (400, `{path}`).
Helpers: `canonical_project_path(raw) -> Path`, `default_project_name(path) -> str`.

Entities referenced, not redefined: `domain/registry.py::RegisteredProject`,
`REGISTERED_PROJECT_ID_PATTERN`. Contracts referenced: `file_adapter/protocol.py` +
`file_adapter/writer_protocol.py` (the port shape mirrored), `errors.py::FactoryConsoleError`,
ARCHITECTURE.md Cross-cutting concurrency house rule.

DB ops: none in this ticket (the Protocol only names them); the UNIQUE index on `projects.path` from
T105 is the duplicate authority. NFR flags: synchronous port — the backend MUST offload every call
with `anyio.to_thread.run_sync` (T98); the DI provider RAISES when unbound, matching
`get_file_adapter`; auth/rate-limit N/A (loopback-only; the v2 write token remains the only write
credential).

## Verification

`python -m pytest tests/unit/test_store_paths.py tests/unit/test_registry_protocol.py -q`;
`python -m pytest -q --cov=factory_console`; `make lint`. No runtime behaviour changes, so
`python -m pytest tests/integration -q` must be unchanged and green.
