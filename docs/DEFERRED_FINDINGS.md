# Deferred findings

Real issues that a review or QA pass **confirmed** but deliberately did **not** fix, because
they fell below the blocking bar (advisory severity, or a fix outside the pass's safe
minimal-diff scope). Nothing here blocked a merge. Nothing here is speculative — each entry
was adversarially verified before being recorded.

Compiled 2026-08-07 from `.factory/qa/result-*.json` (58 findings across 16 QA passes, 9
unfixed) and the team-lead review notes posted on the ticket PRs. Committed at v3.0 close;
`.factory/` is not tracked in git, so without this file the findings behind it are lost with
the working tree.

**Two are worth promoting to tickets rather than leaving on this list:** **D3**, the only
entry here that corrupts user data rather than merely reading awkwardly, and **D5**, now at
three hand-rolled copies of one concurrency mechanic.

**How to use this file.** Pick items off it into real tickets when the surrounding area is
next touched — most are cheapest to fix by whoever is already editing that file. Delete an
entry when it lands; note the PR number if you want the history.

---

## Correctness — worth fixing

### ~~D1. `SqliteProjectRegistry.set_selected_project` is not transactional~~ — FIXED
- **Where:** `server/factory_console/store/sqlite_registry.py:244`
- **Severity:** medium · confidence 76 · from T108 (PR #225)
- **Resolved** by part-4 QA in `7ad675c` (PR #226): the SELECT and UPDATE are now wrapped in
  one `BEGIN IMMEDIATE`/`COMMIT` with an explicit ROLLBACK path.

The existence check and the `UPDATE` ran as two separate autocommit statements. A concurrent
`remove_project` landing between them surfaced a raw `sqlite3.IntegrityError` instead of the
port's named `ProjectNotRegistered`. That's a contract break: callers branch on the named
error, and the raw one escapes as a 500.

### D2. `SqliteProjectRegistry._insert_conflict` re-reads outside a transaction
- **Where:** `server/factory_console/store/sqlite_registry.py:315`
- **Severity:** low · confidence 70 · from T108 (PR #225)

The re-read of the conflicting row is a second autocommit statement. A concurrent delete of
that row in between makes a duplicate-**path** conflict get misreported as an **id**
collision — a wrong reason on an otherwise correct rejection.

> D1 and D2 shared one root cause: neither multi-statement method was wrapped in an explicit
> transaction. D1 is now fixed; **D2 still stands**, and the fix is the same one-line pattern
> `set_selected_project` now uses — copy it.

### D3. Multi-entry `provides` collapses to a comma-joined scalar on edit
- **Where:** `frontend/src/lib/components/EditTicketModal.svelte`
- **Severity:** medium · confidence 75 · from the v2.x frontend QA pass

A genuine multi-entry `provides` list — a **supported read shape** — flattens into a single
comma-joined string on any edit. This is the one entry here that silently corrupts user data
rather than merely reading awkwardly. It went unfixed because the wire contract's `provides`
is a scalar, so a real fix needs a backend + codegen change beyond a QA pass's scope.

**Recommend promoting this one to a ticket rather than leaving it on this list.**

### D12. `_resolve_current` hand-copies the selection precedence it doesn't own
- **Where:** `server/factory_console/api/v1/projects.py`
- **Severity:** medium · confidence 100 · from part-5 QA (PR #230)

`/projects/current` re-implements steps 1–3 of the precedence that `get_current_project_root`
owns, sharing only the final probe. The two paths were **verified to agree today**, so this is
drift risk, not a live bug — but `/projects/current` can silently diverge from what `/project`,
`/graph` and `/roadmap` serve. Left unfixed because extracting the shared seam is a design
change on correct code. Belongs to whichever ticket next touches that seam.

### D13. Unbounded `observer.join()` now runs during live serving
- **Where:** `server/factory_console/app.py` → `FileWatcher.stop()`
- **Severity:** medium · confidence 75 · from part-5 QA (PR #230)

The join has no timeout and, as of T114, runs on **every project switch** from the shared anyio
worker pool — not just at shutdown. A wedged observer would permanently consume a pool slot.
Precondition is rare; bounding it needs a named `Settings` value and changes shutdown
semantics, so **T128 (devops) is the right owner**.

---

## Layering / naming (part-5, all advisory)

### D14. `RealProjectConditionProbe` is constructed in handlers, not injected
- **Where:** `server/factory_console/api/v1/projects.py`
- **Severity:** medium · confidence 100 · from part-5 QA (PR #230)

Unlike every other port. The handler docstring already ratifies the choice, but
`PROJECT_STRUCTURE.md`'s sanctioned-exception list does not name it. Either add the DI seam or
amend the layering doc — a decision for the owning ticket, not a QA minimal diff.

### D15. `projects.py` imports module-private helpers from `deps.py`
- **Where:** `server/factory_console/api/v1/projects.py`
- **Severity:** medium · confidence 75 · from part-5 QA (PR #230)

Imports `_probe_root` and `_read_registry`. The sanctioned exception covers what `_probe_root`
**does**, not **who may import it**, and `_read_registry` has no exception at all. Pure naming
— promote both to public names or widen the exception.

---

## Type safety

### D4. `toTicketUpdate` asserts a return type it can't structurally guarantee
- **Where:** `frontend/src/lib/forms/ticketForm.ts`
- **Severity:** medium · confidence 75

`as TicketUpdate` papers over the fact that `provides` may be omitted. An honest, looser
return type would drop the assertion. Related to D3 — likely fixed in the same pass.

---

## Architecture / duplication

### D5. The write-token park/resume mechanic is hand-rolled ~~twice~~ **three times**
- **Where:** `frontend/src/lib/components/EditTicketModal.svelte`, the ticket detail route,
  and — as of T124 — `frontend/src/lib/components/AddProjectForm.svelte`
- **Severity:** medium · confidence 80

Latch + derived-needed + store-watching effect are implemented independently in all three
places, differing only in resume-vs-drop policy. Two copies of a subtle concurrency mechanic
is how they drift; three is the point at which extracting it stops being optional.

**Updated 2026-08-07 (part 10).** Part-10 QA raised this independently against
`AddProjectForm.svelte` and declined to fix it — correctly, since collapsing three copies into
one `useWriteTokenGate` helper is a refactor across three components, not an in-branch QA fix.
It wants its own ticket.

### D6. Delete orchestration left inline in the route
- **Where:** `frontend/src/routes/tickets/[id]/+page.svelte`
- **Severity:** medium · confidence 75

Token gate, rejection banner, confirm dialog and cross-navigation guards sit inline in the
route, while the equivalent-complexity **edit** flow was extracted into its own component.
Inconsistent, and the inline version is the harder one to test.

### D7. Two near-identical write-route test files, one word-swap apart
- **Where:** `tests/integration/test_api_write_tickets.py` (T73, 637 lines) and
  `tests/integration/test_api_tickets_write.py` (T65, 578 lines)
- **Severity:** medium

Same three write routes, names one word-swap apart. QA deliberately left this alone rather
than renaming: the T73 ticket spec names that exact path as a critical file and in its
verification command, so a rename would have put the ticket and the tree out of sync. A
15-line docstring at the top of each saying which is which is the cheap fix; a merge is the
real one.

---

## `ModalShell` cluster

All four are advisory, all in `frontend/src/lib/components/ModalShell.svelte`, and all are
best handled in one sitting by whoever builds the next dialog on this shell.

### D8. Stacking comment overclaims
The comment says "only the topmost dialog holds focus", but the guard
(`wrapper.contains(activeElement)`) would also fire for an outer shell if a future dialog
nested another `ModalShell` in its body. **Not reachable today** — neither `ConfirmDialog` nor
`DiffPreviewModal` accepts children, so nesting is impossible. It becomes reachable the first
time one does.

### D9. Unreachable dead branch in the Tab trap
`focusableItems()` always returns at least the never-disabled backdrop button, and
`handleKeydown` already bails when `wrapper` is null — so the `items.length === 0` guard
cannot be hit. Confirmed real, no behavioural defect. Either drop it or make it reachable.

### D10. No dedicated spec for `ModalShell`
Its shell contract is re-asserted in both consumer specs; the forward-Tab branch for a panel
with no focusable children is covered by neither. Add `ModalShell.test.ts`.

### D11. Two defensive branches unreachable from either consumer
Empty focusable list, empty panel. Same call as D9: cover them or drop them. Overlaps D10 —
one `ModalShell.test.ts` with a no-focusable-controls body resolves D10, D11 and the testing
half of D9.

---

## Also worth knowing (not findings, but recorded here so they aren't rediscovered)

- **`scripts/smoke.sh` has a startup-timing race in the sandbox.** The server binds and serves
  correctly, just later than the script's fixed poll window. Confirmed by manual probe during
  T108. The script's poll should retry with backoff instead of using a fixed window.
- **`tests/integration/test_app_lifespan.py` has a pre-existing flake**, seen during T108's
  verification with a zero-diff to that file on the branch. Unrelated to v3.0.
- **`cli.py` passes no `watcher_factory`**, so a real project switch degrades to watcher-less
  until **T119** wires it. Not a defect — T119 explicitly owns that line — but it means live
  updates do not follow a switch yet.
- **`frontend/src/lib/api/types.ts` types `projectRoot` as non-null** against the now-nullable
  field. Stale but harmless: codegen is a manual script with no CI gate and no component reads
  the field. **T121** owns regenerating the client.
- **`FAC_BUILD_TOOLS` needed `make` added** (`.factory/factory.config`). Without it a lane
  whose ticket declares `scripts/smoke.sh` in `## Verification` cannot run its own acceptance
  check and reports `build-failed` — this cost T108 two failed lane runs. Fixed 2026-08-07.
