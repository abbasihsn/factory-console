# [T59] Atomic co-writer — apply a planned change-set via tmp-write + os.replace

milestone: v2 · track: file-adapter · depends_on: T13, T15, T57 · provides: file_adapter/atomic_write.py — apply_changes(project, planned) writing the three coupled files as one logical change; partial failure leaves no half-written set; hard-refuses any run-state path.

## Context

Keeps `tickets.json` + the ticket `.md` + `ROADMAP.md` consistent as ONE logical change. Each file is written to a sibling temp file then `os.replace`-renamed into place (atomic on POSIX), and a delete is an unlink; if any step fails the operation aborts before mutating the live files where possible, so a crash never leaves a half-written trio the reader would choke on. This is the only sanctioned WRITE site in the whole console, and it enforces — as a second, independent defense on top of write-render — that the console NEVER writes into the factory run-state directory.

## Staged approach

1. CREATE `server/factory_console/file_adapter/atomic_write.py`.
2. Define `AtomicWriteError(FactoryConsoleError)` (500) for an I/O failure mid-apply.
3. Define `apply_changes(project: Project, planned: list[PlannedChange]) -> list[str]` returning the project-relative POSIX paths written/deleted.
4. GUARD FIRST: for every `PlannedChange` resolve its path and assert it is (a) contained under `project.rootPath` (reuse `PathTraversal` from `file_adapter.path_safety`, T13) and (b) NOT under `project.runStateDir` nor under any `RUN_STATE_RELATIVE_LOCATIONS` (import the tuple from `file_adapter.run_state`, T15) — raise if violated, before any write.
5. Two-phase apply: for each non-delete change write `newText` to a temp file (`tempfile.mkstemp` in the SAME directory as the target, utf-8), fsync, then `os.replace(tmp, target)` creating parent dirs as needed; for each delete change unlink the target if present. Order: manifest, then `.md`, then roadmap.
6. On any exception, clean up leftover temp files and re-raise as `AtomicWriteError`; document the single-writer/single-worker Uvicorn assumption (ARCHITECTURE "Concurrency") so no lock is needed.
7. Never `open()` the run-state dir. Import by full path; do NOT edit `file_adapter/__init__.py`.

## Critical files

- `server/factory_console/file_adapter/atomic_write.py` (new)
- `tests/unit/test_atomic_write.py` (new)

## Interface & data

`apply_changes(project: Project, planned: list[PlannedChange]) -> list[str]`. By reference: `PlannedChange` (T57); `RUN_STATE_RELATIVE_LOCATIONS` + run-state contract (T15 / ARCHITECTURE "Factory run-state directory") — the writer MUST refuse these; `PathTraversal` containment (from `file_adapter.path_safety`, T13). No DB; the write primitive is tmp-write + `os.replace` (atomic single-file swap). NFR: atomicity/all-or-nothing, single-worker Uvicorn (no locks), MUST NOT write under `runStateDir` (hard guard), fsync durability.

## Verification

`pytest tests/unit/test_atomic_write.py` against tmp_path: `apply_changes` writes all three files and re-reading matches `newText`; a delete change unlinks the `.md`; a PlannedChange targeting a path under `.factory/run-state` raises before any write (assert the run-state dir untouched); simulate an `os.replace` failure on the 2nd file (monkeypatch) and assert no temp files are left dangling and the error surfaces as `AtomicWriteError`. Integration: after apply, the existing `RealFileAdapter` re-reads the project cleanly (round-trip).
