# [T86] v3 docs — correct the run-state claim, document runs, spend and the one lint gate

milestone: v3 · track: docs · depends_on: T80, T82, T83, T84, T85 · provides: `architecture.md` corrected on the run-state source and state vocabulary, `usage.md` gaining the runs and spend views plus the changed editability rule, and `README.md` reflecting what the console now shows.

## Context

Every other v3 ticket fixes something the docs currently assert. `docs/architecture.md:45` describes a *"Factory run-state **directory** (read-only) — the factory-owned directory the console probes to map each ticket to a run-state (`todo` / `in_flight` / `ready` / `merged` / `unknown`)"*. Measured: the factory writes a JSON **file**, and its state vocabulary is nine values with no `in_flight` among them. Both halves of that sentence are wrong, and the code was built from it.

So this is not a routine docs refresh. The correction is the deliverable: the docs were the upstream cause of T78 and T80, and leaving them describing the old layout would let the next reader rebuild the same defect. Say what was believed, what is on disk, and how it was established — a corrected document that quietly reads as though it was always right teaches nothing about how to avoid the next one.

There is also a **behaviour change to document, not just a feature**: after T80, a ticket present in `tickets.json` but absent from a run-state that exists is no longer editable in the console. An operator who adds a ticket by hand between factory runs will hit this. It belongs in `usage.md` as a stated rule with its reason, not left to be discovered as a bug report.

## Staged approach

1. `docs/architecture.md`: replace the run-state bullet. The console resolves a run-state **source** — `.factory/run-state.json` first, then the legacy marker directories — and reports which it used. State the factory's real vocabulary (`todo`, `in_progress`, `ready`, `in_part`, `in_submilestone`, `merged`, `flagged`, `failed`, `needs_human`) plus the console's own `unknown` (no source) and `absent` (source exists, ticket not in it). Keep an explicit note that the previous text described a directory the factory does not write, so the correction is visible as a correction.
2. `docs/architecture.md`: add the read-only factory artefacts the console now consumes — ledger, results, receipts, last-stop — and the rule that governs all of them: **`.factory/` is gitignored, so every one is optional and a missing source is rendered as missing, never as zero or empty.**
3. `docs/usage.md`: add the `/runs` and `/spend` views — what each shows, and what the no-run-data state means for someone reading a fresh clone. Include the attributed-cost rule, since a per-ticket column that over-sums the total looks like a bug until it is explained.
4. `docs/usage.md`: update the editing section for T80. `todo` and no-run-state-source remain editable; a ticket absent from a run-state that exists is refused, and the message names the file consulted. Give the hand-added-ticket workflow as the concrete case.
5. `docs/usage.md` (or CONTRIBUTING, wherever the dev loop lives): `make lint` runs `pre-commit run --all-files` and is the same gate CI runs. State that hooks are added to `.pre-commit-config.yaml` only, and why — the Makefile used to enumerate its own set and the two drifted in both directions.
6. `README.md`: extend the capability summary from browse-and-edit to include run and spend visibility, one line each, linking to `usage.md`.
7. Check every other doc for the same claim before closing: any file repeating "run-state directory" or the five-state vocabulary is part of this ticket. **A correction applied to one of several copies leaves the wrong one to be found first.**

## Critical files

- `docs/architecture.md`
- `docs/usage.md`
- `README.md`

## Interface & data

Prose only. No code, no schema, no runtime change. Terms fixed by this ticket and used consistently across all three files: **run-state source** (the resolved file or directory), **`unknown`** (no source), **`absent`** (source exists, ticket not listed), **attributed cost** (a multi-ticket lane charged in full to each id).

## Verification

`grep` the whole `docs/` tree and `README.md` for `run-state directory`, `in_flight`, and `in-flight` and confirm every remaining occurrence is either inside an explicit historical note or genuinely about the legacy directory source — **this grep is the verification, because the failure mode of a docs correction is a surviving copy, and reading only the files you edited cannot detect one.** Confirm the nine factory statuses in `architecture.md` match `FAC_STATES` in `app-factory/lib/common.sh` value for value, and that `unknown`/`absent` are documented as console-side states rather than factory ones. Every documented command runs as written on a clean checkout: `make lint`, and the console's own `/runs` and `/spend` against a project with and without `.factory/`. `make lint` green.
