# [T93] Ledger discovery must use the same errno split the run-state reader made normative

milestone: v2.2 · track: backend · depends_on: T79 · source: Incremental Integration Audit run 2 (2026-08-02), lens `duplicated-or-contradictory-implementation`, tickets T79,T80

## Context

T79's ledger discovery probes a `.factory` artifact with a raw `Path.is_file()`, while T80 made the discovery rule normative and implemented it via the module's own errno split. Two readers of the same tree therefore disagree about an unprobeable path.

**Found by the audit, not by a review.** It takes more than one Ticket to state, which is the
test §10.2 uses for whether a finding belongs to the integration audit at all — no single
Ticket's own review could have seen it.

**Non-blocking, and that is the audit's verdict rather than a convenience.** Run 2 returned
`blocking: 0` and the loop decided `stop — no open blocking findings remain`, so v2.1's Version
is not gated on this. It is filed against **v2.2** so the finding survives: `.factory/` is
gitignored, and the audit's own draft would otherwise exist only on one machine's disk.

## Acceptance criteria

1. `find_ledger_path` probes through the same errno-split helpers T80 made normative (`_node_exists` / `_is_regular_file`), not raw `Path.is_file()`.
2. An unprobeable ledger path is reported as unreadable, NOT as absent — the same 'could not look' vs 'looked and found nothing' distinction T80 spent four amendments on.
3. A test asserts the CONVERSE: an EACCES ledger path does not read as 'no ledger'.
4. CPython 3.13's non-raising `Path.exists()`/`is_dir()` (gh-113978) is not assumed anywhere.
