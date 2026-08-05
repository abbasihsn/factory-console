# [T94] User and contributor docs still state the pre-T78 run-state contract

milestone: v2.2 · track: docs · depends_on: T80 · source: Incremental Integration Audit run 2 (2026-08-02), lens `architecture-drift`, tickets T77,T78,T80

## Context

The user- and contributor-facing docs still describe edit and delete sharing one `todo`/`unknown` gate, `unknown` meaning 'no run-state directory', and a four-value state vocabulary. T80 updated `README.md` and `docs/planning/ARCHITECTURE.md` for exactly those changes and left these behind.

**Found by the audit, not by a review.** It takes more than one Ticket to state, which is the
test §10.2 uses for whether a finding belongs to the integration audit at all — no single
Ticket's own review could have seen it.

**Non-blocking, and that is the audit's verdict rather than a convenience.** Run 2 returned
`blocking: 0` and the loop decided `stop — no open blocking findings remain`, so v2.1's Version
is not gated on this. It is filed against **v2.2** so the finding survives: `.factory/` is
gitignored, and the audit's own draft would otherwise exist only on one machine's disk.

**Narrowed 2026-08-05 by the v2.1 integration audit (finding F6), per T93's precedent.** As filed,
the Context above asserts that `docs/usage.md` and `docs/architecture.md` still describe one shared
edit/delete gate and a four-value vocabulary. **T86 has since corrected both** — `docs/usage.md:88-96`
and `:155-186`, `docs/architecture.md:49-59`. A lane dispatched against this text as written would go
looking for a defect that is no longer there, and the honest failure mode of that is not "wasted
time" but a lane that finds *something* to change in order to have changed something.

**What remains is criterion 4, and only criterion 4**: the converse check. T86 corrected the docs it
knew about; nothing has yet asserted that no superseded phrasing survives anywhere else. That check
has already caught real residue once — T86's own verification enumerated `docs/`, `README.md` and
`CLAUDE.md` while calling itself *"the whole repository"*, and three stale citations survived it in
`server/` docstrings, two of them line-wrapped and so invisible to a single-line grep. So criteria 1-3
are now **verification steps**, not work, and 4 is the ticket.

## Acceptance criteria

1. ~~Every doc stating the run-state contract names the same vocabulary the code implements.~~
   **Verify only** — T86 did this. Report the files checked; change nothing that is already correct.
2. `unknown` is documented as 'nothing was said' and `unreadable` as 'the information is unavailable' — the wording T80's Amendment 4 settled on.
3. Edit and delete are documented as SEPARATE gates (`ensure_mutable` vs `ensure_deletable`), because they are.
4. A grep for the superseded phrasings returns nothing outside historical records.
