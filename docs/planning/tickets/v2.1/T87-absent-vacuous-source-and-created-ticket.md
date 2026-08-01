# [T87] Two gaps in T80's absent gate: a vacuous source, and a ticket the console just created

milestone: v2.1 · track: backend · depends_on: T80 · provides: a run-state source that lists nothing resolves `unknown` rather than `absent`, so an untouched project stays editable; and a ticket the run-state does not track can be deleted, so a mistyped creation is recoverable.

## Context

T80's deep review left two high findings open **by design** — both need a product decision rather than an auto-fix, and this ticket is that decision. Neither is a defect in what T80 chose; both are cases its rule did not consider.

**T80's rule is deliberate and survives this ticket unchanged:** a run-state source that lists *other* tickets but not this one means the source is authoritative and does not know this ticket, so the console refuses to write it. T80's own text anticipates the consequence — *"an operator who adds a ticket by hand between factory runs can no longer edit it in the console until the factory seeds it"* — and accepts it. **This ticket does not reopen that.**

### Gap 1 — a source that exists but lists nothing makes the whole project read-only

`probe_ticket_state` ends `return RunState.absent` for any readable run-state directory with no marker for the id. So an **empty but valid** run-state directory resolves `absent` for *every* ticket, and `MUTABLE_STATES = (todo, unknown)` excludes `absent`, so every write raises `TicketNotMutable` (409).

Measured directly, on T80's branch:

```
empty-but-valid run-state dir, probe T01  -> RunState.absent
                               probe T99  -> RunState.absent
                               probe ANY  -> RunState.absent
```

**This collides with T80's own stated invariant**, in T80's own words: *"Keeping `unknown → mutable` is deliberate and must survive this ticket: it is what lets the console work on a plan the factory has never touched. Removing it would make the console useless on a fresh project."*

A source that names nobody is a plan the factory has never touched. T80 reasoned about *"a source exists, this ticket is not in it"* — which presumes the source lists **something**. When it lists nothing, there is no authority being exercised, only an empty directory that happens to be present. **A source that names nobody says nothing about anybody.**

### Gap 2 — a ticket the console just created cannot be un-created

`create_ticket` applies no gate. `edit_ticket` and `delete_ticket` gate first. So the moment a ticket is created, its id resolves `absent` (the run-state has never heard of it) and **both edit and delete return 409**. A mistyped new ticket is unrecoverable through the UI that created it.

This differs from the hand-added case T80 accepted in one way that matters: **the console itself created it.** An operator who adds a ticket by hand can reasonably be told the factory owns it now; an operator who mistypes a name in the console's own create form, and is then refused the delete button next to it, is looking at a trap the console built.

Note the asymmetry is real and not theoretical: `create` is ungated precisely so a fresh ticket can be added, and the gate then refuses to undo what it just permitted.

## Staged approach

1. **`run_state.py` — a vacuous directory source resolves `unknown`.** After the marker loop finds nothing, check whether the directory contains *any* marker for *any* ticket. If it contains none, return `unknown` (nothing to ask) rather than `absent` (asked, not listed). Keep the existing `unknown` returns for the unreadable/vanished cases exactly as they are — those already distinguish "could not tell" correctly.
2. **`read_json_run_state` / `probe_ticket_state_from_source` — the same rule for the JSON form.** A `tickets` object that parsed correctly and is **empty** is a vacuous source: every probe is `unknown`, not `absent`. A `tickets` object with entries, queried for an id it lacks, stays `absent` — T80's rule, untouched.
3. **Permit `delete` on `absent`.** Deleting a ticket the run-state does not track cannot desynchronise the factory: there is no run-state entry to orphan. Edit stays refused, so T80's rule holds for the hand-added case; only the *undo* path opens. Implement as a gate variant (`ensure_deletable`) rather than by widening `MUTABLE_STATES`, so the permission is visible and cannot leak into the edit path.
4. **Audit consumers for the widened behaviour**, as T80 step 5 did: `editability.ts` must still treat `absent` as non-editable, while the delete affordance follows the new rule. An allowlist that silently gained a member is the failure mode.

## Critical files

- `server/factory_console/file_adapter/run_state.py`
- `server/factory_console/file_adapter/write_gate.py`
- `server/factory_console/file_adapter/real_writer.py`
- `frontend/src/lib/forms/editability.ts`

## Interface & data

No new enum member and no schema change. `probe_ticket_state` and `probe_ticket_state_from_source` return `unknown` for a *vacuous* resolved source and `absent` only for a source with at least one entry that lacks this id. `MUTABLE_STATES` unchanged at `(todo, unknown)`. New `ensure_deletable(project, ticket_id) -> RunState` permitting `absent` in addition to `MUTABLE_STATES`; `ensure_mutable` unchanged.

## Verification

Assert the **behaviour and the resulting state**, never the error wording — T80's ticket names this as the recurring defect in this program, and it applies here unchanged.

Required cases:

- empty run-state **directory**, probe any id → `unknown`, and `ensure_mutable` **permits** — the regression guard for gap 1;
- run-state directory with a marker for `T01` only, probe `T02` → `absent`, `ensure_mutable` **refuses** — **T80's rule, asserted as still true**, and the test that fails if this ticket over-corrects;
- `run-state.json` with `tickets: {}` → `unknown`, permits;
- `run-state.json` with one entry, probe a different id → `absent`, refuses;
- create-then-delete a ticket in a project with a populated run-state source → delete **succeeds**;
- create-then-edit the same ticket → still **refused** (`absent`), proving gap 2's fix did not widen the edit gate;
- `isEditable('absent') === false` unchanged.

`make lint`, `pytest`, `pnpm check`, `pnpm test` green.
