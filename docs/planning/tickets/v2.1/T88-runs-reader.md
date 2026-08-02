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

---

## Amendment 1 — a refusal must name its own cause, on every path

**Decided 2026-08-02, after round 4 hit the tier's iteration cap with one item open.**

Round 4 left this, at confidence 60 — below the auto-apply gate, so it was correctly left for a human:

> A symlinked `.factory` yields `400 invalid_ticket_id` via results/receipts but `unreadable` via
> `read_last_stop`, for the identical condition. Deliberate and documented, but **the 400 blames a
> well-formed ticket id.**

**It is not a documentation question, and the decision is: fix it.** This is the same defect class the
run-state work spent four amendments on, one module over. T80's invariant was that a refusal must
*name the value* it could not interpret, because "not tracked" and "could not be read" send an
operator to different fixes. This is that rule applied to a different pair of causes:

> **An error must name the condition that actually occurred. Two code paths observing the same
> condition must report it the same way.**

A `400 invalid_ticket_id` for a well-formed id is worse than an unhelpful message — it is an
**accusation about the wrong thing**. The operator checks the id, finds it correct, and has been sent
away from the symlinked `.factory` that is the real cause.

### What to change

1. **The condition, not the caller, decides the answer.** An unreadable/unsafe `.factory` resolves the
   same way from every reader in this module — results, receipts, and last-stop alike. If one path
   surfaces it as `unreadable`, they all do.
2. **`invalid_ticket_id` is reserved for an id that is actually invalid.** A well-formed id must never
   produce it. Assert the converse: a well-formed id against a symlinked `.factory` does **not** return
   `invalid_ticket_id`.
3. **The refusal names the cause** — the path that could not be used, in the same shape the run-state
   409 names its status value.
4. Nothing else in this Ticket's scope changes. This is one inconsistency, stated once, with the
   answer given; it is **not** an invitation to re-review the four rounds already completed.

### Why this is a resume and not a split

T80 was split at its cap because it had **grown** — six fail-opens across four amendments, each round
adding surface. T88 has not grown: four rounds, each finding real defects in the *same* module
(a symlink escape, a resolve/stat-open TOCTOU, a FIFO hang, an AST-guard from-import bypass), and one
narrow item left with a knowable answer. DL-060's test is *"has the thing being reviewed grown past
what one round can cover?"* — here it has not, so the correct move is the one round it needs.
