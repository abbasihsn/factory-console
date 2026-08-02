# [T80] Write gate: split "no run-state source" from "absent from a source that exists"

milestone: v2.1 · track: backend · depends_on: T78 · provides: `ensure_mutable` refuses a ticket that a present run-state source does not list, while a project with no run-state source at all stays fully editable.

## Context

`write_gate.MUTABLE_STATES = (RunState.todo, RunState.unknown)`. Measured against this repository before T78: `find_run_state_dir()` returns `None`, every ticket resolves to `unknown`, and therefore **every ticket — including all 73 merged ones — passes the gate.** The gate is not bypassed; it never engages, because it can never see a state.

T78 fixes the reading, which fixes this repository. It does not fix the rule. `unknown` still means two different things:

| on disk | what should happen |
|---|---|
| no run-state source of any kind | mutable — nothing claims otherwise |
| a source exists, this ticket is not in it | **refuse** — the source is authoritative and does not list this ticket |

**The authoritative contract currently mandates the wrong answer for the second case.** `docs/planning/ARCHITECTURE.md` §"Factory run-state directory (read-only)" says *"Present dir but missing marker → `RunState.todo`"* — and `todo` is mutable. So this is not only a defect in the JSON path T78 adds; it is the specified behaviour of the directory path too, and a project using the legacy directory form has the same hole. **This ticket changes the contract, not just the code**, and T86 is where the document catches up. Implementing it without that amendment would leave the code and its contract disagreeing, which is the condition this whole milestone exists to remove.

The second case is not hypothetical after T78: `.factory/run-state.json` lists the tickets the factory has seeded, and a ticket added to `tickets.json` by hand after the last factory run is absent from it. So is a ticket whose id was mistyped. Under today's rule both are editable, and under the fixed rule the first still is — via the seed path, not via a blanket `unknown`.

Keeping `unknown → mutable` is deliberate and must survive this ticket: it is what lets the console work on a plan the factory has never touched. Removing it would make the console useless on a fresh project. **The defect is the conflation, not the permission.**

This is the eighth time in this program an empty result has had to be made distinguishable from an unasked question. The fix is the same one every time: give the absence a name.

## Staged approach

1. Add `RunState.absent = "absent"` — "a run-state source exists and does not list this ticket". Distinct from `unknown`, which after this ticket means only "no source to ask".
2. In `probe_ticket_state_from_source` (T78), return `absent` when the source resolved but the ticket has no entry, and `unknown` only when `source is None`. The two now have different values at the point where the information exists, rather than being reconstructed later from context — a caller that has already collapsed them cannot recover the difference.
3. `MUTABLE_STATES` stays `(todo, unknown)`. `absent` is not added. That single line is the behaviour change; everything above it exists to make the line correct.
4. `ensure_mutable` raises its existing not-mutable error for `absent`, with a distinct message: the ticket is not known to the run-state at `<source path>`, so the console will not write it. Name the resolved source path — an operator seeing a refusal needs to know which file was consulted, especially when the answer is "the file you are not looking at".
5. Audit every other `RunState` consumer for a total-looking match that is now non-total: `editability.ts`, `RunStateBadge.svelte`, any dict keyed by state. `isEditable` is an allowlist so `absent` is read-only for free — assert it rather than relying on it.
6. Confirm the seed path still works: a ticket in `tickets.json` but not in the run-state, in a project with a run-state source, is now refused. **That is a behaviour change for a real workflow** — an operator who adds a ticket by hand between factory runs can no longer edit it in the console until the factory seeds it. Document it in the ticket's PR body and in `usage.md` (T86) rather than discovering it in use.

## Critical files

- `server/factory_console/file_adapter/write_gate.py`
- `server/factory_console/file_adapter/run_state.py`
- `server/factory_console/domain/run_state.py`
- `frontend/src/lib/forms/editability.ts`

## Interface & data

`RunState` gains `absent = "absent"`. `probe_ticket_state_from_source(source, ticket_id)` returns `unknown` iff `source is None`, `absent` iff the source resolved and has no entry for the id. `MUTABLE_STATES` unchanged at `(RunState.todo, RunState.unknown)`. `ensure_mutable(project, ticket_id) -> RunState` raises the existing gate error for `absent`, message naming `project.runStateSource.path`. No new endpoint, no schema-breaking change beyond the added enum member (additive; the generated frontend union widens).

## Verification

Pytest `test_write_gate.py` additions, each stated as the behaviour and not the wording — **an assertion that matches an error message's text while claiming to test a refusal is the recurring defect in this program; assert the raised type and the resulting state, and check the message separately if at all.** Cases: source present + ticket listed `merged` → refused; source present + ticket absent → refused, state is `absent`; **source absent entirely → still mutable** (this is the regression guard for the deliberate permission, and it is the one a fix aimed only at "stop letting merged tickets through" would break); ticket listed `todo` → mutable. A test at the API level that a PUT to a merged ticket in a project with a real `run-state.json` returns the gate's error status — pre-T78 that request succeeded, so this is the end-to-end proof. Vitest: `isEditable('absent') === false`; `RunStateBadge` renders `absent`. `make lint`, `pytest`, `pnpm check`, `pnpm test` green.

---

## Amendment, 2026-08-01 — two cases the rule above did not consider

T80's deep review (2 rounds) left **two high findings open by design**, both marked as needing a
product decision rather than an auto-fix. The decision was taken on 2026-08-01 and is recorded here,
in this ticket, because **both gaps are in this ticket's own code and must be closed before it
merges**. They were briefly drafted as a follow-up ticket (T87); that was a planning error — a fix
ticket cannot depend on the merge it is required to unblock.

**The rule above is unchanged.** A source that lists *other* tickets but not this one still refuses
the write, for the reasons already given, including the accepted consequence for hand-added tickets.

### Gap 1 — a source that lists NOTHING makes the whole project read-only

`probe_ticket_state` ends `return RunState.absent` for any readable run-state directory with no
marker. An **empty but valid** run-state directory therefore resolves `absent` for *every* ticket,
and every write raises `TicketNotMutable` (409). Measured on this branch:

```
empty-but-valid run-state dir, probe T01 -> RunState.absent
                               probe T99 -> RunState.absent
                               probe ANY -> RunState.absent
```

**This collides with this ticket's own stated invariant**, quoted from §Context above: *"Keeping
`unknown → mutable` is deliberate and must survive this ticket: it is what lets the console work on a
plan the factory has never touched."*

The rule reasoned about *"a source exists, this ticket is not in it"*, which presumes the source lists
**something**. When it lists nothing, no authority is being exercised. **A source that names nobody
says nothing about anybody.**

**Fix:** a *vacuous* resolved source — a run-state directory containing no marker for any ticket, or a
`tickets` object that parsed and is empty — resolves `unknown`, not `absent`. A source with at least
one entry, queried for an id it lacks, still resolves `absent`.

### Gap 2 — the console cannot un-create its own ticket

`create_ticket` applies no gate; `edit_ticket` and `delete_ticket` gate first. So a newly created id
resolves `absent` immediately and **both edit and delete return 409**. A mistyped new ticket is
unrecoverable through the UI that created it.

This differs from the hand-added case accepted above in the way that matters: **the console itself
created it.** `create` is ungated precisely so a fresh ticket can be added, and the gate then refuses
to undo what it just permitted.

**Fix:** permit `delete` on `absent` via a separate `ensure_deletable`, **not** by widening
`MUTABLE_STATES`. Deleting a ticket the run-state does not track cannot orphan a run-state entry.
Edit stays refused, so the rule above holds.

### Verification for the amendment

Added to §Verification, same discipline — assert behaviour and resulting state, never wording:

- empty run-state **directory**, probe any id → `unknown`, `ensure_mutable` **permits** — gap 1's guard;
- directory with a marker for `T01` only, probe `T02` → `absent`, **refuses** — **the original rule,
  asserted as still true; this is the test that fails if the amendment over-corrects**;
- `run-state.json` with `tickets: {}` → `unknown`, permits;
- `run-state.json` with one entry, probe a different id → `absent`, refuses;
- create-then-**delete** in a project with a populated source → **succeeds**;
- create-then-**edit** the same ticket → still **refused**, proving the edit gate did not widen;
- `isEditable('absent') === false` unchanged.

---

## Amendment 2, 2026-08-02 — an unreadable source must not grant write access

T80's third review round (the first to run after **DL-062** unblocked the nested reviewer) left one
high finding open, correctly identifying it as a gate-policy decision rather than a mechanical fix:

> An unreadable run-state source fails **OPEN** to the mutable `unknown` at the write gate — **`main`
> failed closed with a 500.** `run_state.py:675`

**This is a regression in safety posture relative to `main`, not a design choice**, and it is the
**third instance of one conflation.** `unknown` has been carrying three different meanings:

| Situation | What it means | Correct answer |
|---|---|---|
| No run-state source at all | nothing claims anything about this ticket | **mutable** — the ticket's original, deliberate rule |
| A source that lists nobody | a source that names nobody says nothing about anybody | **mutable** — Amendment 1, gap 1 |
| **A source that cannot be READ** | **something claims, and we could not see what** | **REFUSE** |

The first two are "I looked and there is nothing to find." The third is **"I could not look"** — and
this program has settled that distinction repeatedly, always the same way:

- **INV-42** — a check that could not be **executed** is `fail`, never `passed`;
- `verification-policy.ts` — a command that fails policy becomes `human_verification_required`,
  never a silent skip;
- `sessions.ts` — an `unverifiable` stop **refuses** to release a worktree, because *"unverifiable is
  not stopped"*;
- **DL-058** — *absence of a record is not absence of a process.*

**An unreadable source is `unverifiable`, and unverifiable fails closed.** Granting write access
because a permission error prevented us from checking is the one direction this program has never
allowed anywhere else.

### Fix

At the point where the source is resolved but its contents cannot be read (EACCES and friends),
return a state the write gate **refuses**, distinct from both `unknown` and `absent`, and report it as
such: the operator needs to know the answer is *"the run-state at `<path>` could not be read"*, not
*"this ticket is not tracked"*. Those are different problems with different fixes.

**Do not reach this by removing `unknown → mutable`.** That rule is load-bearing for a project the
factory has never touched, and Amendment 1 restated it deliberately. The change is to stop routing
the unreadable case through it.

### Verification

- an unreadable run-state directory (chmod 000, or a source whose read raises OSError) → the write
  gate **refuses**, and the error names the **source path**;
- **the refusal is distinguishable from `absent`** — asserted on the resulting state, not on message
  text, so a reader can tell "could not read" from "not listed";
- a project with **no** source at all → still **mutable** (the original rule, and the regression guard
  for this amendment);
- a **vacuous** source → still **mutable** (Amendment 1, gap 1, unchanged);
- a source listing another ticket, probed for this one → still **absent**, still refused (the original
  rule, unchanged);
- `main`'s prior behaviour is not restored literally: it returned a **500**, which is a crash rather
  than a decision. The correct answer is a deliberate refusal at the gate, not an unhandled error.

---

## Amendment 3, 2026-08-02 — state the invariant once, instead of a fourth special case

The review after Amendment 2 left one high open, and it is the **fourth instance of a single
conflation** this ticket has now been amended for three times:

> `_marker_state` resolves a stale lower-precedence marker (e.g. `todo`) as the ticket's state when a
> **higher-precedence** state directory (e.g. `merged`) is **unreadable**, rather than refusing.
> `run_state.py:303`

A merged ticket can therefore read as `todo` — **mutable** — because the directory that would have
said `merged` could not be read.

### The pattern, and why a fourth patch is the wrong move

| # | Case | Resolution |
|---|---|---|
| 1 | No run-state source at all | mutable — the original rule |
| 2 | A source that lists nobody | mutable — Amendment 1 |
| 3 | A source that cannot be read at all | refuse — Amendment 2 |
| 4 | A source **partly** unreadable, at a higher precedence than the marker found | **this** |

Each was found only after the previous one shipped. **That is the diagnosis: the ticket has been
patched case by case for a rule it never stated.** A fifth case will exist — nested precedence,
symlinked state dirs, a marker under a directory that vanishes mid-probe — and it will be found the
same way, one review round after it is introduced.

So this amendment states the rule, and the residual is one of its consequences rather than its point:

> **THE RESOLUTION INVARIANT.** A run-state resolution that **could not read** something it needed
> must **refuse**. It may never fall back to a state that is *more permissive* than the one it failed
> to check.
>
> "I looked and found nothing" and "I could not look" are different answers. Only the first may
> return a mutable state.

This is the program's own rule, stated everywhere else and never here: **INV-42** (a check that could
not be executed is `fail`, never `passed`), `verification-policy.ts`
(`human_verification_required`, never a silent skip), `sessions.ts` (*"unverifiable is not
stopped"*), **DL-058** (*absence of a record is not absence of a process*).

### What to build

1. Apply the invariant in `_marker_state`: if **any** directory at a precedence **at or above** the
   marker actually found could not be read, refuse — do not return the lower marker. The bound is
   *"at or above"*, because a lower-precedence directory being unreadable cannot change an answer
   already determined by a higher one.
2. **Audit every other resolution path in `run_state.py` against the invariant in the same pass**, and
   list them in the PR body with their verdict. This is what makes this amendment different from the
   previous three: the point is to close the *class*, not the instance. A path that is already correct
   is a finding too — record it as checked.
3. The existing test that pins today's behaviour is **superseded**, not deleted: rewrite it to assert
   the refusal, and keep its comment explaining why the old answer looked reasonable.

### Verification

- a `merged` directory that cannot be read, with a stale `todo` marker present → **refuses**; the
  ticket does not read as `todo`;
- the inverse bound: an unreadable **lower**-precedence directory, with a readable higher-precedence
  marker → **resolves normally**, because the answer was already determined. This is the test that
  fails if the fix over-refuses;
- all three earlier cases unchanged: no source → mutable · vacuous → mutable · wholly unreadable →
  refuse;
- CPython **3.13**: the EACCES-raising behaviour `Path.exists()`/`is_dir()` lost in gh-113978 must not
  be assumed anywhere the invariant is enforced — this round already found one such site, and the
  audit in step 2 must confirm there are no others.

### The audit (Amendment 3, step 2) — every resolution path, with its verdict

Required by step 2: *"Audit every other resolution path in `run_state.py` against the invariant in the
same pass... A path that is already correct is a finding too — record it as checked."* Every site that
can turn a filesystem answer into a `RunState`, in resolution order:

| # | Path | Verdict |
|---|---|---|
| 1 | `_node_exists` / `_is_directory` / `_is_regular_file` — the errno split | **DEFECT, fixed this round.** `_ABSENT_ERRNOS` included `ELOOP`, so a symlink loop answered "definitively absent" instead of raising. See below. |
| 2 | `find_run_state_source` — discovery | **CHECKED.** A candidate that could not be probed resolves *to* that candidate and refuses, rather than being skipped into `None` (the mutable `unknown`). |
| 3 | `find_run_state_dir` | **CHECKED, no production caller.** A `None` here does not mean "no directory exists"; nothing derives a safety decision from it (`atomic_write` forbids all documented locations unconditionally). |
| 4 | `_directory_lists_any_ticket` — vacuity | **CHECKED.** Three-way `bool \| None`: `ENOENT`/`ENOTDIR` are definitive, every other `OSError` degrades to `None` ("could not tell"), which the caller refuses rather than reading as vacuity. |
| 5 | `_marker_state` — the precedence walk | **CHECKED.** `OSError` propagates; the "at or above" bound is free from returning on the first hit, so an unreadable directory *below* a determined answer cannot change it. |
| 6 | `probe_ticket_state` | **CHECKED, and NO PRODUCTION CALLER** — see the open finding below. |
| 7 | `read_json_run_state` — document-level failures | **CHECKED, with a ratified residual.** A vanished file and content that could not be *parsed* resolve the mutable `unknown`; only an existing file whose bytes could not be read sets `unreadable`. The parse-failure half is ratified by `ARCHITECTURE.md` addendum (amendment 2) but sits in tension with the invariant — see the open finding below. |
| 8 | `run_state_resolver` → `resolve_json` — per-entry failures | **RESIDUAL, NOT RATIFIED.** An entry that names *this* ticket under a status the console cannot classify resolves the mutable `unknown` via the `known_ticket_ids` arm — see the open finding below. |
| 9 | `run_state_resolver` → `resolve_directory` | **CHECKED.** Unreadable-source canary settled once, vanished-directory re-check on the no-marker path, and `enumeration_failed` carried into the closure so "could not enumerate" never reads as "lists nobody". |
| 10 | `write_gate._ensure_state_allowed` | **CHECKED.** One resolution site, two allowlists, `unreadable` in neither; a fresh resolver per gate call, so vacuity is never stale at gate time. |
| 11 | `RealFileAdapter._safe_run_state` (`real.py`) | **CHECKED, read-only.** `PathTraversal` → `unknown` feeds list/deps/graph badges only; the write gate never routes through it. |

**CPython 3.13 / gh-113978:** confirmed by grep that `run_state.py`, `write_gate.py` and
`domain/run_state_source.py` contain **no** raw `Path.exists()` / `.is_dir()` / `.is_file()` — every
node check goes through the three helpers that own the errno split. There are no other such sites.

### The fifth case, found by the audit and fixed: `ELOOP` is not absence

`_ABSENT_ERRNOS` was `{ENOENT, ENOTDIR, EBADF, ELOOP}`, matching CPython's `pathlib._ignore_error`.
`ELOOP` does not mean "this node is not there" — it means the entry **exists** and could not be
**resolved**. So the fail-open arrived through the errno table rather than through the walk, which is
why amendment 3's fix to `_marker_state` did not cover it:

- a looping `merged/<id>` answered `False` instead of raising, so the walk stepped over it and
  returned the stale `todo/<id>` — the **mutable** state — for a ticket the factory may have merged;
- a looping run-state **directory** answered `False` from `_is_directory`, which `run_state_resolver`
  reads as "not a directory" and turns into the mutable `unknown` for **every** ticket in the project.

Neither propagated, so no `OSError` guard ever saw them. `ELOOP` is now excluded and both cases
refuse. Nothing is lost: a *dangling* symlink still answers `ENOENT` and is still ordinary absence
(pinned by `test_a_missing_marker_is_still_absent_not_unreadable`). `EBADF` stays — a path-based
`stat()` cannot raise it, so it is inert. Guarded by
`test_a_looping_higher_precedence_state_dir_refuses_rather_than_reading_the_stale_marker` and its
over-refusal inverse, `test_a_looping_lower_precedence_state_dir_does_not_change_a_higher_marker`.

### Open, and needing a decision rather than an auto-fix

Recorded here in the pattern the three amendments above established — a gate-policy question is
decided by a human and written into this ticket, not silently patched.

1. **An entry that names this ticket under an unclassifiable status resolves MUTABLE** (`resolve_json`,
   the `ticket_id in parsed.known_ticket_ids` arm). The file parsed fine and *does* list this ticket;
   only its `status` could not be mapped. By the invariant's own wording that is "the source claims,
   and we could not see what" → **REFUSE** — the status we failed to read could have been `merged`.
   Reachable without corruption: the factory gaining a tenth `FAC_STATES` member, a schema drift to
   `{"T42": "merged"}` (status as the value, not an object), a `status` that is not a string. Note
   `JsonRunState.unrecognised` is written and consumed by nothing, so the "visible gap" this is
   defended by is a log line only. Currently pinned as intended behaviour by
   `test_run_state_source.py`; overturning it means changing those tests, the two docstrings, and
   ARCHITECTURE.md. **This is the fourth instance of the conflation, and the case the invariant
   covers most plainly — it should probably become amendment 4.**
2. **A `run-state.json` that exists but could not be PARSED makes the whole project mutable.** The
   `ARCHITECTURE.md` addendum (amendment 2) explicitly ratifies this (`unknown` covers "content that
   could not be parsed"), so unlike (1) it is a contract change, not just a code change. Worth
   re-deciding anyway: the factory writes this file from another process, so a truncated or
   half-written file makes every ticket it lists as `merged` editable for the length of the window.
3. **`probe_ticket_state` has no production caller** (~130 lines re-deriving the same invariant that
   `resolve_directory` implements independently), while `probe_ticket_state_from_source` — the write
   gate's and the ticket-detail read's path — goes through `run_state_resolver` and therefore pays the
   batch form's eager canary + full `_directory_lists_any_ticket` enumeration for a **single** id,
   where `main` paid up to four `stat()` calls. So the duplication amendment 3 set out to remove is
   still there, and the single-ticket path pays the batch cost. The two available fixes pull in
   opposite directions — collapse `probe_ticket_state` into a wrapper (removes the duplication, keeps
   the eager cost) or route the single-id path back through it (removes the cost, keeps the
   duplication) — which is why this is a design decision and not an auto-fix.

---

## Amendment 4, 2026-08-02 — an unclassifiable status is unavailable information, and the last of the class

The round after Amendment 3 found a **fifth** instance by rule rather than by accident (ELOOP treated
as definitively-absent, letting a looping `merged/` fall through to a stale mutable `todo/`) and
surfaced a **sixth**:

> `resolve_json` routes an entry that names this ticket under an **unclassifiable status** to the
> mutable `unknown`. `run_state.py:795`

**Amendment 3 worked.** These were found by applying the stated invariant to every resolution path,
not by a review happening to trip over them. That is the difference between closing a class and
patching an instance, and it is why this amendment can be the last policy decision the class admits.

### The decision, and a correction to the invariant's wording

Amendment 3 phrased the rule as *"could not **read** something it needed."* This case does not fit
that phrasing: the file was read successfully. We read `status: <value>` and **could not interpret
it**. Looked, saw, did not understand — which is not the same as not looking.

**The conclusion is unchanged, and the wording is what needs widening:**

> **THE RESOLUTION INVARIANT (restated).** A run-state resolution must refuse whenever the
> information it needed is **unavailable** — whether because it could not be read, or because it was
> read and could not be interpreted. It may never fall back to a state *more permissive* than the one
> it failed to establish.

An unrecognised status is the factory saying something about this ticket in a vocabulary this console
does not know. **The one thing it is not is silence.** `read_json_run_state` already treats it as
signal — it collects unrecognised values into `unrecognised` precisely so *"a tenth factory state
must be visible as a named gap, never silently dropped."* Routing that same value to a **mutable**
state contradicts the collection: the gap is named and then ignored at the only point it matters.

**The concrete failure:** the factory adds a tenth state — say `in_review`. This console does not know
it. A ticket under review resolves `unknown` → mutable → **the console edits a ticket the factory is
actively reviewing.** That is the fail-open this ticket exists to close, arriving through the one door
left open.

### What to build

1. `resolve_json`: an entry that names this ticket under a status outside `FACTORY_STATUS_ALIASES`
   **refuses**, and the refusal names the unrecognised value — an operator needs *"the run-state says
   `in_review`, which this console does not know"*, not *"not tracked"*.
2. **`unknown → mutable` still stands** for its one real case: **no entry for this ticket at all**, in
   a source that lists others → that is `absent`; and **no source at all** → `unknown`, mutable. This
   amendment narrows `unknown` to *"nothing was said"*, never *"something was said that we could not
   read or could not interpret."*
3. The tests, two docstrings and `ARCHITECTURE.md` that pin today's behaviour are **superseded, not
   deleted**: rewrite each to assert the refusal and keep the reasoning that made the old answer look
   right.
4. **State in the PR body that the enumeration is complete** — list every resolution path in
   `run_state.py` with its verdict under the restated invariant, including the ones already correct.
   If a seventh case exists, it is found *now*, by enumeration, and not by a seventh review round.

### Verification

- a JSON entry with an unrecognised status → **refuses**, error names the value;
- the value still appears in `unrecognised` — the naming and the refusal are both required, and a fix
  that refuses while dropping the name has traded one silence for another;
- **no entry at all** in a populated source → still `absent`, still refused (unchanged);
- **no source at all** → still `unknown`, still **mutable** — the regression guard for the rule this
  amendment must not break;
- a vacuous source → still mutable (Amendment 1); a wholly unreadable source → refuses (Amendment 2);
  a higher-precedence unreadable directory → refuses (Amendment 3); ELOOP → refuses;
- CPython 3.13's non-raising `Path.exists()`/`is_dir()` (gh-113978) is not assumed anywhere.

### The audit (Amendment 4, step 4) — every resolution path, re-verdicted under the RESTATED invariant

Required by step 4: *"list every resolution path in `run_state.py` with its verdict under the restated
invariant, including the ones already correct. If a seventh case exists, it is found now, by
enumeration, and not by a seventh review round."* Amendment 3's audit asked "could it have been
**read**?"; this one asks the wider question, "was the information **available** — read AND
interpretable?", which is why two rows move.

| # | Path | Verdict under the restated invariant |
|---|---|---|
| 1 | `_node_exists` / `_is_directory` / `_is_regular_file` — the errno split | **CHECKED.** Unchanged by the restatement: it is purely a read-failure boundary, and `_ABSENT_ERRNOS` already admits only the three errnos that mean "definitively not there". |
| 2 | `find_run_state_source` — discovery | **CHECKED.** A candidate that could not be probed becomes the source and refuses. Nothing is *interpreted* at discovery — only a node type is observed — so the widened clause adds no case here. |
| 3 | `find_run_state_dir` | **CHECKED, no production caller.** Unchanged. |
| 4 | `_directory_lists_any_ticket` — vacuity | **CHECKED.** Three-way `bool \| None`; "could not enumerate" never reads as "lists nobody". |
| 5 | `_marker_state` — the precedence walk | **CHECKED.** A marker directory name IS the state, so a marker either names a state this console has or is not seen at all — the interpretation step the JSON form has does not exist here. But see row 12: *not seeing it* is its own defect. |
| 6 | `probe_ticket_state` | **CHECKED, and still NO PRODUCTION CALLER** (Amendment 3's open item 3, undecided). |
| 7 | `read_json_run_state` — document-level failures | **RESIDUAL, RATIFIED AND NARROWED.** A vanished file and a document that could not be parsed keep the mutable `unknown`. This is the boundary of the restatement, and it holds for a reason rather than by omission: a document that resolved into nothing names **no ticket**, so it makes no claim about the id being asked about — it is silence, not an uninterpretable claim. Amendment 3's open item 2 asks whether the half-written-file window should move it anyway; still a human decision, still open. |
| 8 | `_resolve_json_state` (was `resolve_json`) — per-entry failures | **DEFECT, FIXED THIS ROUND.** The `known_ticket_ids` arm resolved the mutable `unknown` for an entry naming this ticket under an unclassifiable status. It now resolves `unreadable`, and the refusal names the value via `JsonRunState.unclassifiable`. This is the case the amendment was written for. |
| 9 | `run_state_resolver` → `resolve_directory` | **CHECKED.** Canary settled once, vanished re-check on the no-marker path, `enumeration_failed` carried into the closure. |
| 10 | `write_gate._ensure_state_allowed` | **CHECKED.** One resolution site, two allowlists, `unreadable` in neither. Now resolves through `probe_ticket_state_with_reason` — the same single read, so the state and the value the refusal names come from the same bytes. |
| 11 | `RealFileAdapter._safe_run_state` (`real.py`) | **CHECKED, read-only.** `PathTraversal` → `unknown` feeds badges only; the write gate never routes through it. |
| 12 | `_MARKER_PRECEDENCE` — the marker directory's closed state vocabulary | **DEFECT, NOT FIXED — this is the seventh case, and it needs a decision.** See below. |

### The seventh case, found by this enumeration: the directory form has the same hole, one level up

`_MARKER_PRECEDENCE` is a closed 4-tuple, and **both** the vacuity scan and the marker walk iterate
only it. A state subdirectory this console has no name for — `.factory/run-state/in_review/` — is
never opened, never probed and never logged. That is the *directory form's* version of exactly what
row 8 fixes: the factory said something about this ticket in a vocabulary this console does not know.
Two outcomes, both fail-open, both reachable from the same tenth-`FAC_STATES` scenario the amendment
opens with:

- markers exist **only** under unrecognised state directories → `_directory_lists_any_ticket` answers
  `False` → the source reads as **vacuous** → *every* ticket resolves the mutable `unknown`, i.e. the
  write gate is disabled project-wide;
- some tickets have known-state markers and this one is named only under `in_review/` → it resolves
  **`absent`**, which is in `DELETABLE_STATES` → the console **deletes** a ticket a lane owns.

It is **not** auto-fixed here, for the reason this ticket has established three times: it is a
gate-policy question, and unlike row 8 it is not the case the amendment ratified. It also has a real
over-refusal risk that needs deciding rather than guessing — a stray directory under the run-state
dir must not turn a whole project read-only, and the honest per-id rule ("an unrecognised state
directory that names this ticket refuses, even when a known marker also names it, because the unknown
state's precedence is unknown") changes what `merged/<id>` alone is allowed to answer.

**The decision needed:** does an unrecognised state *directory* refuse the ids it names (and, if it is
the only populated one, stop the source reading as vacuous)? If yes, it is amendment 5 and it closes
the class on the directory side too.

---

## The seventh case is SPLIT to T92 — this ticket's scope is now closed

**Decided 2026-08-02, after the fifth review round reached this tier's iteration cap.**

The enumeration required by Amendment 4 step 4 did exactly what it was written to do: it found the
seventh case **by rule**, in row 12 of its own audit table, rather than by a seventh review round.
That is the outcome the step was for, and it means this ticket's method worked.

**What follows from it is a split, not an Amendment 5.** T80 has taken nine lane runs and closed six
instances of one conflation across four amendments. Its fifth review round ended `capped`. That is
the T81 shape (DL-058), and DL-060's rule applies:

> A ticket that has grown past what one review round can cover does not converge by being given
> another round.

Every previous amendment **grew** this ticket. This decision **shrinks** it — to the six cases it has
closed and proved, with 830 backend and 381 frontend tests green. The seventh moves to **T92**, whose
whole diff a reviewer can hold at once, and which carries the gate-policy decision this ticket
correctly refused to make on its own.

**The finding does not escape by being split.** T92 carries milestone `v2.1` and `depends_on: [T80]`,
so §12.2 clause 4 ("no unresolved blocking finding") and the frontier both hold it in front of the
Version. Splitting a finding out of a PR must never be the move that lets it through, and this is the
test of whether it was: T92 must merge before v2.1 does.

### Also open, and deliberately not blocking this ticket

Four lower-severity findings recorded `open` by round 5, none of them in this ticket's class:
`read_json_run_state`'s `MemoryError` gap, ungated `create` against an unreadable source,
`probe_ticket_state`'s 130 lines with no production caller (Amendment 3, open item 3), and
`docs/usage.md` staleness — which **T86 owns by this ticket's own step 6**, and which round 5 correctly
reverted its own fix for once the repo rule was noticed.
