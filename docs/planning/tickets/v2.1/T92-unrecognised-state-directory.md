# [T92] An unrecognised state directory is unavailable information too

milestone: v2.1 · track: backend · depends_on: T80 · provides: the directory run-state form refuses the ids named under a state subdirectory this console has no name for, closing the seventh and last instance of T80's fail-open class.

## Context

**This is T80's seventh case, split out rather than amended in.** It was found the way T80's Amendment 4
step 4 required — *"if a seventh case exists, it is found now, by enumeration, and not by a seventh
review round"* — by the row-by-row audit of every resolution path in `run_state.py` (see T80's
Amendment 4 audit table, row 12).

T80 closed six instances of one conflation across four amendments and nine lane runs. Its review loop
then reached this tier's iteration cap with this case open **by design**: the reviewer declined to
patch it, correctly, because it is a gate-policy question and because the honest fix changes what
`merged/<id>` alone is allowed to answer.

It is split rather than amended for the reason T81 was split (DL-058): **a ticket that has grown past
what one review round can cover does not converge by being given another round.** T80's scope is now
*shrinking* to what it has already closed and proved — 830 tests green — and this case gets a ticket
whose whole diff a reviewer can hold at once. The separability test the audit playbook uses passes
here: this defect is stateable without reference to T80's JSON-form work, because it lives in a
different source implementation.

**It is blocking and it stays blocking.** It carries milestone v2.1, so §12.2 clause 4 ("no unresolved
blocking finding") and the frontier both hold it in front of the Version. Splitting a finding out of a
PR must never be the move that lets it escape, and here it is not: it is a Ticket that must merge
before v2.1 does.

## The defect

`_MARKER_PRECEDENCE` is a closed 4-tuple, and **both** the vacuity scan and the marker walk iterate
only it. A state subdirectory this console has no name for — `.factory/run-state/in_review/` after the
factory adds a tenth `FAC_STATES` entry — is never opened, never probed and never logged. Two
outcomes, both fail-open:

1. **Markers exist only under unrecognised state directories** → `_directory_lists_any_ticket` answers
   `False` → the source reads as **vacuous** → every ticket resolves the mutable `unknown`, i.e. **the
   write gate is disabled project-wide**.
2. **Some tickets have known-state markers, this one is named only under `in_review/`** → it resolves
   **`absent`**, which is in `DELETABLE_STATES` → **the console deletes a ticket a lane owns.**

Both are reachable from the same scenario T80's Amendment 4 opens with, and both are the directory
form's version of exactly what T80 fixed in the JSON form: *the factory said something about this
ticket in a vocabulary this console does not know.*

## The decision this ticket carries

T80 left the policy question open for a human. **Answered here, and it is the same answer the whole
family has:**

> **An unrecognised state directory refuses the ids it names — per id, not per source.**

Two halves, and the split between them is what resolves the over-refusal risk T80 flagged:

- **Per-id refusal, not per-source.** Only ids actually **named** under an unrecognised state
  directory refuse. A stray or empty directory under the run-state dir names nobody, so it changes
  nothing — a misplaced folder must not turn a whole project read-only. This is the narrow rule; the
  broad one ("any unrecognised directory makes the source unreadable") is rejected as over-refusal.
- **It refuses even when a known marker also names the id.** Because the unknown state's **precedence
  is unknown**: `merged/<id>` no longer settles the question once `in_review/<id>` might outrank it.
  This is the clause that changes what `merged/<id>` alone is allowed to answer, and it is the clause
  that makes the fix honest rather than cosmetic.

And the vacuity half follows from the same reasoning: **a source with markers under unrecognised
directories is populated, not vacuous.** It lists tickets; we simply cannot name their states. So
`_directory_lists_any_ticket` must count markers under **every** subdirectory, not only the known
four. Ids named nowhere at all remain `absent` — unchanged, and still the ratified behaviour.

## Scope

Backend only, `server/factory_console/file_adapter/run_state.py` and its domain types. **No frontend
work**: T80 already widened the `unreadable` prose to cover "the information is unavailable", and this
case resolves to that same state, so the badge and gate copy are already correct.

## Acceptance criteria

1. `_directory_lists_any_ticket` counts markers under **every** subdirectory of the run-state dir, so a
   source populated only under unrecognised names does **not** read as vacuous.
2. An id named under an unrecognised state directory resolves the refusing state, **even when a
   recognised marker also names it**.
3. The refusal **names the directory** (`state 'in_review'`), the same way T80's JSON refusal names the
   status value — "not tracked" and "could not be read" send an operator to the wrong fix.
4. An unrecognised directory that names **no** ids changes nothing: the project stays as mutable as it
   was. Asserted as the converse, because over-refusal is the failure mode this narrowness exists to
   prevent.
5. The unrecognised state names are **collected and surfaced**, as the JSON form collects
   `unclassifiable` — a tenth factory state must be visible as a named gap, not merely refused.
6. `DELETE` of a ticket named only under an unrecognised directory is **refused** (it is no longer
   `absent`), with an end-to-end test on the API, not just a unit test on the resolver.
7. Every existing run-state test still passes unchanged. **A test that had to be edited to accommodate
   this change is a regression until argued otherwise** — T80's Amendment 4 rewrote two such tests and
   said so in the diff.

## Out of scope, and deliberately

- `probe_ticket_state`'s 130 lines with no production caller (T80 Amendment 3, open item 3).
- Whether a half-written `run-state.json` should stop reading as mutable `unknown` (T80 Amendment 3,
  open item 2). Both remain open human decisions and neither blocks this.

---

## Amendment 1 — an UNSEARCHABLE unrecognised directory refuses too

**Decided 2026-08-02, on round 2's open high (confidence 25).**

The finding:

> `_unrecognised_state_naming` swallows `OSError` into `unprobeable`, but both callers check for a
> **recognised** marker before consulting it — so a discoverable-but-unsearchable unrecognised state
> directory plus a stale known marker (`todo/<id>`) yields the **mutable** state instead of refusing.

The reviewer scored it 25 and declined to auto-reverse it, because it is a documented tradeoff pinned
by an existing test, with the stated justification of **avoiding a project-wide lockout**. Declining
was right. The tradeoff is wrong.

**The justification is the fail-open reasoning this family has rejected six times.** "Refusing might
lock the project" is the same argument as "an unreadable source should stay editable" (T80 Amendment
2), "a looping `merged/` should fall through to `todo/`" (Amendment 3), and "an unclassifiable status
should resolve mutable" (Amendment 4). Each time the answer was the same, and it is the same here.

**And this ticket's own rule already settles it.** T92 says an unrecognised state directory refuses
the ids it names *even when a recognised marker also names them*, because **the unknown state's
precedence is unknown**. A directory we cannot search is one whose named ids we cannot enumerate — so
for any id, we cannot rule out that it is named there, and `todo/<id>` no longer settles the question.

### The distinction that makes this consistent rather than over-refusing

This ticket's over-refusal guard says *a stray directory under the run-state dir must not turn a whole
project read-only.* That guard is about a directory that names **nobody** — empty, and readable enough
to know it is empty.

**An unsearchable directory is not empty. It is unknown.** That is T80's Amendment 4 distinction
exactly: absent ≠ empty, and *unavailable* covers both unread and read-but-uninterpretable. The guard
protects the empty case and says nothing about this one.

Yes, this means a project whose run-state directory contains an unsearchable subdirectory becomes
read-only until a human fixes the permissions. **That is the correct outcome**: the console cannot
establish who owns those tickets, and it must not edit tickets a lane may own. It is also loud, which
an unnoticed fail-open is not.

### What to change

1. An unsearchable unrecognised state directory resolves the **refusing** state for every id it could
   name — which, since it cannot be enumerated, is every id in that source.
2. This holds **even when a recognised marker also names the id**. Same clause as the base ticket, same
   reason.
3. The refusal **names the directory it could not search**, and says *unsearchable* rather than
   *unrecognised* — they need different fixes (`chmod` vs a console update).
4. **The existing test pinning the mutable answer is rewritten to assert the refusal, keeping the
   comment that made the old answer look reasonable.** T80 did this twice; the reasoning is the most
   valuable part of a superseded test, because it records why the wrong answer was convincing.
5. The empty-but-readable stray-directory case is unchanged, and a test asserts that converse
   explicitly — this amendment must not be read as licence to refuse whenever anything is unfamiliar.
