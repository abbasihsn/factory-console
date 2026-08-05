# [T97] One bounded artifact read, not three copies of it

milestone: v2.2 · track: backend · depends_on: T86 · source: T86 deep review round 2 (2026-08-05), finding `37a4d62a4014`, medium/75

## Context

T86's review consolidated `ABSENT_ERRNOS` into `path_safety.py` because two modules held
byte-identical copies under a comment promising to keep them in step by hand. It then left the
**larger** copy one layer up untouched.

`ledger.read_ledger` and `runs._read_json_artifact` each independently implement the identical
sequence — `os.open` with `O_NONBLOCK`, `os.fstat`, `S_ISREG` refusal, size-cap check, bounded
read, guarded close — differing only in the byte cap and the log text. **Both docstrings
acknowledge mirroring each other**, which is the tell: a comment that asks a future reader to
maintain a correspondence by hand is the same drift hazard `path_safety.py` was created to remove,
and it is doing more work here than it was doing for the errno set.

The hazard is not hypothetical. This exact sequence exists because of a real defect T86's review
found: a `stat`-then-`open` of the same *name* let a FIFO substituted in between stat as
`st_size == 0`, pass the cap, and block forever on an `async` handler's event loop — hanging every
route in the app. A tightening applied to one copy of that gate and not the other leaves one
artifact reader hardened and its sibling exposed, with nothing to say which is which.

**Non-blocking.** Round 2 returned `any_high_open: 0` and the loop reached its convergence
condition; v2.1's Version is not gated on this. It is filed so the finding survives the lane that
found it.

## Acceptance criteria

1. ONE bounded-read helper, parameterised by byte cap and a caller label, consumed by both
   `ledger.read_ledger` and `runs._read_json_artifact`. Neither retains its own copy of the
   open/fstat/`S_ISREG`/cap/bounded-read sequence.
2. It lives beside `resolve_or_none` and `ABSENT_ERRNOS` in `path_safety.py`, or in a sibling
   module named for the job — not in either of its two current callers, since a helper owned by one
   caller is a helper the other will re-copy.
3. A test that the helper refuses a **FIFO** and a **directory**, and one that a file crossing the
   cap is reported as a named skip rather than short-read. These assert the threat model, not the
   refactor: a consolidation that preserved the sequence but lost a gate would pass a
   behaviour-agnostic test.
4. The guard test asserting these modules contain no filesystem-mutating call still passes for the
   new location.
5. Fold in finding `b05f77c23318` (low/85, same module): `read_ledger` logs the ledger path with
   `%r` at seven sites while `find_ledger_path` and the omitted-lines record use `%s`, so the same
   path appears as `PosixPath('/p/.factory/metrics/ledger.jsonl')` in some records and bare in
   others and an operator grepping for it misses half. The comment justifying `%r` scopes it to the
   **cause**, not the path.
