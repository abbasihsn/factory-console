# [T109] ProjectConditionProbe — named per-project conditions, established from disk

milestone: v3.0 · track: file-adapter · depends_on: T103, T11, T88, T89 · provides: `file_adapter/project_condition.py` — a small read-only port (Protocol + Real + Fake, mirroring `RunArtifactReader`) answering each registered path with exactly one NAMED condition, resolved most-degraded-first and never raising.

## Context

A registry row is a claim the user made; whether it is still true is a question about the filesystem,
asked fresh on every read. This ticket answers it, and it is the ticket that keeps v3.0 honest:
MONOTONICITY and the missing-must-render-as-missing rule both bind here. A registered path that was
deleted, renamed, made unreadable, or that stopped being a factory project must render as a NAMED
condition — never silently absent from the list (the user would conclude they never registered it)
and never a 500 (one bad row would take out the whole project switcher). The five names are fixed by
T103; this ticket establishes them from disk.

**It is a PORT rather than a bare function** for the reason `run_artifacts.py` spells out at length:
a service calling a probe function directly would stat the host filesystem no matter which registry
it was handed, so every fake-backed test — which seeds paths like `/factory/demo-project` that do not
exist — would answer `path_missing` for everything while appearing to be under test. That is the same
"blank field produced by our own wiring" failure the v2.2 milestone existed to abolish. So the module
holds Protocol + Real + Fake together, exactly as `run_artifacts.py` does, and is deliberately NOT
re-exported from `file_adapter/__init__.py` — consumers import by full path, so this ticket touches
no aggregation file.

**It lives in `file_adapter/` and not in `store/` on purpose**, and that placement is the ownership
story working rather than being bent: this is a read of the TARGET PROJECT's files, which is
precisely what file-adapter owns and what the store must not do. It carries the literal READ-ONLY
banner and is pinned by `tests/_read_only_guard.py`.

## Staged approach

1. CREATE `server/factory_console/file_adapter/project_condition.py` with the
   `# READ-ONLY: this module MUST NOT write, create, or delete.` banner. Module docstring: why this
   is a port (the `run_artifacts` argument above), why it lives in `file_adapter/` and not `store/`,
   that it is TOTAL — it never raises for a source-level problem — and that it is not re-exported
   from the package `__init__`.
2. Define the pure classifier `classify_project_path(path: Path) -> RegistryEntryCondition`,
   resolving **MOST-DEGRADED-FIRST** and reusing `discovery.MANIFEST_RELPATH` rather than re-spelling
   `docs/planning/tickets.json`:
   - stat the path — `FileNotFoundError`/`NotADirectoryError` on the path itself → `path_missing`;
     any other `OSError` (EACCES, ELOOP, …) → `unreadable`;
   - not a directory → `not_a_project`;
   - `is_file()` on `path / MANIFEST_RELPATH` — `OSError` → `unreadable`, False → `not_a_project`;
   - `.factory/` directory absent → `no_factory_dir`;
   - else `ok`.
   Docstring the precedence explicitly and state the rule it enforces: **a permission error is NEVER
   answered as the more permissive `not_a_project`**, because "I could not look" is not "I looked and
   it is not a project".
3. Define `@runtime_checkable class ProjectConditionProbe(Protocol)` with the single method
   `probe(self, path: Path) -> RegistryEntryCondition`, documented as TOTAL — a conforming
   implementation never lets an exception escape, so one bad row cannot fail a whole listing.
4. Define `class RealProjectConditionProbe` delegating to the classifier, and
   `class FakeProjectConditionProbe(conditions: dict[Path, RegistryEntryCondition], default:
   RegistryEntryCondition = "ok")` answering from a seeded map with NO filesystem access,
   canonicalizing lookup keys so a test's spelling does not matter.
5. CREATE `tests/unit/test_project_condition.py`: drive the classifier against the existing
   fixtures — `tests/fixtures/projects/factory_layout/` → `ok` (it has `.factory/`),
   `tests/fixtures/projects/minimal/` → `no_factory_dir` — and against `tmp_path` for the rest: a
   never-created path → `path_missing`, a regular file → `not_a_project`, an empty dir →
   `not_a_project`, a dir whose manifest is missing → `not_a_project`, a `chmod(0o000)` directory →
   `unreadable` (skip when running as root, where the mode is not enforced), and a project dir with a
   manifest but no `.factory/` → `no_factory_dir`. Assert the probe NEVER raises for any of these.
   Cover all five members of the union. Add `assert_module_is_read_only` from
   `tests/_read_only_guard.py` for this module.
6. Do NOT modify `server/factory_console/file_adapter/__init__.py` — following `watcher.py` and
   `run_artifacts.py`, this module is imported by full path.

## Critical files

- `server/factory_console/file_adapter/project_condition.py` (create)
- `tests/unit/test_project_condition.py` (create)

## Interface & data

Port (NEW, read-only, TOTAL): `ProjectConditionProbe` with `probe(path: Path) ->
RegistryEntryCondition`; implementations `RealProjectConditionProbe()` and
`FakeProjectConditionProbe(conditions, default="ok")`; pure classifier
`classify_project_path(path: Path) -> RegistryEntryCondition`.

Condition values (defined in T103, established here): `ok` · `path_missing` (deleted, renamed or
moved) · `not_a_project` (exists and is readable but holds no `docs/planning/tickets.json`) ·
`unreadable` (exists but could not be examined) · `no_factory_dir` (a real factory project with no
`.factory/`; degraded-but-usable — run-state, runs and spend are legitimately missing, never zero).

Referenced, not redefined: `domain/registry.py::RegistryEntryCondition`,
`file_adapter/discovery.py::MANIFEST_RELPATH`, `file_adapter/run_artifacts.py` (the
small-port-with-both-implementations shape mirrored), ARCHITECTURE.md "Other factory artefacts"
(missing-must-render-as-missing) and "The resolution invariant" (MONOTONICITY).

DB ops: none — this ticket reads target-project paths only and never touches the console DB.
NFR flags: read-only module pinned by `tests/_read_only_guard.py`; blocking I/O, offloaded by the
backend with anyio (T98); TOTAL port — never raises, so one bad row cannot fail a listing; every
returned value is a modelled `Literal`, so the disclosure rule needs no allowlist entry.

## Verification

`python -m pytest tests/unit/test_project_condition.py -q`;
`python -m pytest -q --cov=factory_console` (85% gate); `make lint`.
Regression: `python -m pytest tests/integration/test_disclosure_policy.py
tests/integration/test_cli.py -q` and `scripts/smoke.sh` — the console must still serve a single
project unchanged.
