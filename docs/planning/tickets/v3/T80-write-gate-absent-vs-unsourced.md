# [T80] Write gate: split "no run-state source" from "absent from a source that exists"

milestone: v3 · track: backend · depends_on: T78 · provides: `ensure_mutable` refuses a ticket that a present run-state source does not list, while a project with no run-state source at all stays fully editable.

## Context

`write_gate.MUTABLE_STATES = (RunState.todo, RunState.unknown)`. Measured against this repository before T78: `find_run_state_dir()` returns `None`, every ticket resolves to `unknown`, and therefore **every ticket — including all 73 merged ones — passes the gate.** The gate is not bypassed; it never engages, because it can never see a state.

T78 fixes the reading, which fixes this repository. It does not fix the rule. `unknown` still means two different things:

| on disk | what should happen |
|---|---|
| no run-state source of any kind | mutable — nothing claims otherwise |
| a source exists, this ticket is not in it | **refuse** — the source is authoritative and does not list this ticket |

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
