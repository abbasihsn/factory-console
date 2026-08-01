# [T88] Read the factory's lane artifacts beside run-state

milestone: v2.1 · track: backend · depends_on: T78 · provides: `file_adapter/runs.py` reads a project's lane results, receipts and last-stop artifacts as typed records, tolerant of every absent or malformed file, writing nothing.

## Context

First of three tickets replacing **T81**, which was split after measurement rather than opinion: across four lane runs T81 absorbed **33 review fixes without converging**, and a run with the turn ceiling raised to 2400 **hit the 90-minute wall clock** before the ceiling — so turns were never the binding constraint. The ticket was too large to review as one unit. See DL-058.

**The prior work is salvage material, not a starting point to trust.** `rescue/T81-regressed-c11052f` holds T81's full history including 33 review fixes; `tkt/T81` is reset to `176801c` (822 tests green, one review round). Read them, take what survives review, and re-derive the rest. A branch that was reviewed but never receipted has no merge authority, and inheriting its code without re-reviewing it inherits that gap.

This ticket is the **reading layer only**. It answers "what artifacts exist and what do they say", and nothing about how a run is presented.

## Staged approach

1. `file_adapter/runs.py`: locate a project's `.factory/results/*.json`, `.factory/receipts/*.json`, and the last-stop artifact. Each is optional; each may be malformed.
2. Every read returns a typed result carrying **why** it is empty — absent, unreadable, or unparseable — never a bare `None`. This program has repeatedly paid for an empty result that could not be told from an unasked question; the ledger reader (T79) already sets the pattern with `LedgerRead`/`SkippedLine` and this ticket follows it.
3. A size cap per file, as T79 has, so a pathological artifact cannot be read into memory unbounded.
4. A guard test asserting this module contains **no filesystem-mutating call** — the same assertion `run_state.py` carries.

## Critical files

- `server/factory_console/file_adapter/runs.py`
- `server/factory_console/file_adapter/path_safety.py`
- `tests/fixtures/runs/`

## Interface & data

New module `file_adapter/runs.py`. Reader functions return result objects that distinguish absent / unreadable / unparseable from empty-but-valid. No domain model, no service, no endpoint — those are T89 and T90. No schema change.

## Verification

Pytest: each artifact absent → typed empty with a stated reason; each malformed → typed empty with a *different* stated reason, asserted as distinct from absent; a valid artifact → parsed fields; the size cap trips on an oversized file; the no-write guard test passes. `make lint`, `pytest` green.
