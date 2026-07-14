# [T11] Upward-walk project discovery (discovery.py + ProjectNotFound)

milestone: MVP · track: file-adapter · depends_on: T07 · provides: `discover_project(explicit, cwd) -> Path`; `ProjectNotFound` exception (FactoryConsoleError subclass, status 404, code `project_not_found`)

## Context

CLI needs git-style path discovery: if user passes `PATH`, use it; else walk up from cwd until `docs/planning/tickets.json` is found. Isolating in this module keeps CLI a thin shell and enables deterministic `tmp_path` unit tests.

## Staged approach

1. `file_adapter/discovery.py`.
2. `class ProjectNotFound(FactoryConsoleError)`: `status=404`, `code='project_not_found'`; `__init__` takes `starting_dir: Path` and sets message accordingly.
3. `find_project_root(start: Path) -> Path`: resolve `start`, walk parents (including start) checking for `docs/planning/tickets.json`; stop at fs root; raise `ProjectNotFound(start)`. Handle symlinks via `resolve(strict=False)` at entry.
4. `discover_project(explicit: Path | None, cwd: Path) -> Path`: if `explicit` given, verify `docs/planning/tickets.json` under it (else raise `ProjectNotFound`); else `find_project_root(cwd)`.
5. `tests/unit/test_discovery.py` using `tmp_path`: found at cwd; found N levels up; not found raises; explicit missing manifest raises; explicit-with-manifest returns as-is; symlink resolved.

## Critical files

- `server/factory_console/file_adapter/discovery.py`
- `tests/unit/test_discovery.py`

## Interface & data

Consumes `Path`; returns `Path`. Implements "PATH arg wins else upward-walk from cwd" clause of CLI contract. Raises `ProjectNotFound` (FactoryConsoleError subclass) mapped to CLI exit code 1 by T25 and to HTTP 404 by T20's exception handler.

## Verification

`pytest tests/unit/test_discovery.py -q` green including multi-level upward walk; ruff clean.
