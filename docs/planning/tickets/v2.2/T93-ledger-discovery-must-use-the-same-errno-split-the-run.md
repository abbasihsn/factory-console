# [T93] Ledger discovery must use the same errno split the run-state reader made normative

milestone: v2.2 · track: backend · depends_on: T79 · source: Incremental Integration Audit run 2 (2026-08-02), lens `duplicated-or-contradictory-implementation`, tickets T79,T80

## Context

**Narrowed 2026-08-04 — the original defect is already fixed; what remains is smaller.** As filed, this ticket said *"T79's ledger discovery probes a `.factory` artifact with a raw `Path.is_file()`"*. That is no longer true: the T86 branch's review-fix commit rewrote `find_ledger_path` to `stat()` the candidate directly, catch `OSError`, and re-raise anything outside the shared `ABSENT_ERRNOS` set — which that same commit hoisted into `file_adapter/path_safety.py` from the byte-identical copies in `ledger.py` and `run_state.py`. `tests/unit/test_ledger.py::test_a_probe_that_could_not_look_raises_rather_than_reporting_absence` pins the converse. Left unedited, this ticket would send a future lane to re-diagnose a defect that is not there.

**The residual is real and worth keeping.** The two readers now share the errno *constant* but not the *probe*: `find_ledger_path` still spells out its own `stat` + `S_ISREG` + errno-split inline rather than calling `run_state.py`'s `_is_regular_file`, which owns the same contract. That is one shared decision with two implementations — the drift hazard `path_safety.py` exists to close, met one level up. It is smaller than the original finding and no longer a disagreement about behaviour, only about ownership.

**Found by the audit, not by a review.** It takes more than one Ticket to state, which is the
test §10.2 uses for whether a finding belongs to the integration audit at all — no single
Ticket's own review could have seen it.

**Non-blocking, and that is the audit's verdict rather than a convenience.** Run 2 returned
`blocking: 0` and the loop decided `stop — no open blocking findings remain`, so v2.1's Version
is not gated on this. It is filed against **v2.2** so the finding survives: `.factory/` is
gitignored, and the audit's own draft would otherwise exist only on one machine's disk.

## Acceptance criteria

1. `find_ledger_path` probes through the same errno-split HELPER T80 made normative (`_is_regular_file`), rather than restating the `stat` + `S_ISREG` + errno-split inline. Sharing `ABSENT_ERRNOS` alone is not enough: the constant is one of the two things that can drift, and the surrounding logic is the other.
2. Wherever that helper lands, it is reachable from both `ledger.py` and `run_state.py` without either importing the other's private surface — `file_adapter/path_safety.py` already owns this class of rule and is the obvious home.
3. The behaviour below is unchanged and still pinned by the tests that pin it today (`test_a_probe_that_could_not_look_raises_rather_than_reporting_absence`, `test_a_file_where_a_parent_directory_belongs_is_absence_not_a_failure`): an unprobeable ledger path is reported as unreadable, NOT as absent — the 'could not look' vs 'looked and found nothing' distinction T80 spent four amendments on. This ticket is a consolidation, so a behaviour change here is a regression.
4. CPython 3.13's non-raising `Path.exists()`/`is_dir()` (gh-113978) is not assumed anywhere.

### Already satisfied (do not redo)

- The raw `Path.is_file()` probe is gone; `find_ledger_path` raises rather than collapsing "I could not look" into `None`.
- `ABSENT_ERRNOS` is defined once in `path_safety.py` and imported by both readers.
- The converse test exists.
