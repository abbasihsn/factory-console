# [T101] The containment refusal sends the operator to check two things that are not at fault

milestone: v2.2 · track: fullstack · depends_on: T86 · source: Incremental Integration Audit run 3 (2026-08-05), lens `cross-ticket-regression`, tickets T82,T84,T86

## Context

T86 added `LedgerNotContained` (`ledger.py:103-113`, `:178-186`) — raised when the ledger resolves
outside the project root. Correct, and the guard it belongs to is a real one.

`spend.py:81-96` catches it as `OSError` and answers `source.found: true, read: false` with
`skipped=[{lineNo: 0, reason: "unreadable"}]` — **byte-identical** to the answer it gives for a file
over the size cap and for a failed probe. `spend/+page.svelte:117-128` renders that single branch as:

> The console found a ledger at `<path>` but could not open it — it is over the reader's size cap, or
> unreadable.

So an operator whose `.factory` is a symlink out of the project is told to go check a size cap and
file permissions. Both are fine. The actual cause — the console refused to follow a path out of the
project root — is not mentioned, and is not discoverable from anything the page shows.

**This is not a new rule; it is a rule this release wrote down.** `ARCHITECTURE.md:262-274` states
it: the authorization answer may be shared while the remedy differs, and the refusal must name the
remedy. And it is the same defect T88's Amendment 1 fixed and T96 is filed to fix class-wide — *an
error must name the condition that actually occurred* — arriving from the opposite direction. T88's
version returned the wrong cause for a valid input. This one returns a **true** cause that is too
coarse to act on: three conditions with three different remedies collapsed into one sentence naming
two of them.

**The judgement this needs, stated up front.** `unreadable` is deliberately coarse at the API
boundary — that is T82's design, and widening what a read-only endpoint discloses about the
filesystem is not free. So the answer is probably not "add a `not_contained` reason to the public
envelope" by default. It may be that the remedy belongs in a log line and the UI sentence stops
claiming to enumerate causes it cannot distinguish. Deciding that is the work.

**Found by the audit, not by a review.** T82 built the envelope, T84 built the rendering, T86 added
the third cause. Each is correct against the contract it was written against.

**Non-blocking.** Run 3 returned `blocking: 0`; v2.1's Version is not gated on this.

## Acceptance criteria

1. A ledger that resolves outside the project root produces an operator-visible signal that names
   **containment** as the cause. Where that signal lives — API field, log line, or UI copy — is this
   Ticket's decision to make and to record, not a given.
2. If the public envelope is left coarse, the UI sentence no longer enumerates causes it cannot
   distinguish. *"over the size cap, or unreadable"* is a claim about which conditions are possible,
   and it is now false.
3. Whatever is decided applies to all three routes into the found-but-unread branch — cap exceeded,
   probe failed, not contained — rather than special-casing the newest one. A fourth cause added
   later must not be able to inherit a third cause's remedy silently.
4. A test asserting the containment case is distinguishable from the size-cap case by whatever
   channel criterion 1 chose. Not a test that both return `read: false`, which passes today.
5. `ARCHITECTURE.md:262-274`'s rule is cited by the test or the code, so the next reader finds the
   rule from the code rather than the other way round.
