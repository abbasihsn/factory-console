# [T95] The ledger artifact is read but never watched

milestone: v2.2 · track: backend · depends_on: T91 · source: Incremental Integration Audit run 2 (2026-08-02), lens `end-to-end-behaviour`, tickets T79,T91

## Context

T91 landed as the fix for 'a factory artifact the console reads is not watched, so live updates are silently dead', and scoped itself to run-state. T79 added a reader for a NEW factory artifact in the same range that the watcher does not schedule, and no Ticket covers it. The audit found the incompleteness of its own fix.

**Found by the audit, not by a review.** It takes more than one Ticket to state, which is the
test §10.2 uses for whether a finding belongs to the integration audit at all — no single
Ticket's own review could have seen it.

**Non-blocking, and that is the audit's verdict rather than a convenience.** Run 2 returned
`blocking: 0` and the loop decided `stop — no open blocking findings remain`, so v2.1's Version
is not gated on this. It is filed against **v2.2** so the finding survives: `.factory/` is
gitignored, and the audit's own draft would otherwise exist only on one machine's disk.

## Acceptance criteria

1. The watcher schedules the ledger artifact's location alongside the run-state source.
2. T91's INV-03 trap applies here too: the factory writes via `mktemp` + `mv`, so a naive single-file watch sees the rename and stops observing the new inode. Watch the containing directory, or re-arm on rename.
3. An end-to-end test proves a ledger append fires an SSE event, not merely that the watcher was configured.
4. The set of watched artifacts is derived from ONE list that both the readers and the watcher consume, so the next new artifact cannot be read-but-unwatched by omission.
