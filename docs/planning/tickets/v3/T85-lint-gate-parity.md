# [T85] One lint gate — `make lint` must run what CI runs

milestone: v3 · track: infra-devops · depends_on: — · provides: `make lint` delegating to `pre-commit run --all-files`, so the gate a developer or a build lane can run locally is the same gate CI enforces; plus the one file currently failing it, and the stale handoff note in the hook config.

## Context

This repository has **two lint gates that disagree in both directions.** Both measured on `main`.

**CI** runs `pre-commit run --all-files` (`.github/workflows/ci.yml:64`), which is: `ruff-check`, `ruff-format` — both scoped `files: '^(server|tests)/.*\.py$'` — the generic whitespace/EOF/yaml hooks, `frontend-eslint`, and `frontend-prettier` (`pnpm --dir frontend format:check`). The whole config carries `exclude: '^tests/fixtures/'`.

**`make lint`** runs `ruff check . && ruff format --check . && cd frontend && pnpm lint`. It replicates neither the scoping nor the exclude, and `frontend/package.json` defines `lint` as **eslint only** — `format:check` is a separate script that `make lint` never calls.

The consequences, both reproduced:

1. **`make lint` fails on `main`.** `ruff format --check .` walks into `tests/fixtures/projects/malformed/docs/planning/tickets/foo.md` — a fixture that is malformed *by design* — and reports "1 file would be reformatted". CI never sees it, because pre-commit excludes `tests/fixtures/` and scopes ruff to `.py` under `server|tests`. Exit 2, before `pnpm lint` ever runs.
2. **`make lint` passes a file CI fails.** `frontend/src/lib/forms/ticketForm.test.ts` fails `npx prettier --check`. `pnpm lint` is eslint and does not look at formatting, so `make lint` is silent on it while CI's `frontend-prettier` hook is red.

**The two gates are red and green on disjoint sets.** Passing `make lint` neither implies nor is implied by CI passing, which makes it worse than having no local gate — it returns an answer that is confidently about a different question.

There is a factory dimension. `git log` attributes the unformatted file to `f18b7f8 "fixed 7 review issues"` — a factory review-fix commit. The lane's QA passed it and CI caught it after merge. But the sharper point is that **running `make lint` would not have saved the lane either**, because `make lint` does not check prettier. It is not enough for a lane to run the project's lint command; the project's lint command has to be the gate. The factory-side half of this is recorded in app-factory's decision log; this ticket is only this repository's half.

## Staged approach

1. Replace the `lint` recipe with `pre-commit run --all-files`. **One authority for one rule.** A Makefile that re-derives the hook set is a second authority that will drift from the first — it already has, in two directions, which is the evidence for the choice rather than an argument for it.
2. Keep the recipe usable without a pre-commit install: if `pre-commit` is not on `PATH`, fail with a message naming `pip install -e '.[dev]'` and `pre-commit install`. Do not fall back to the old hand-rolled command — a fallback that checks something different is how the two gates diverged, and a silent fallback would rebuild the defect this ticket removes.
3. Add `pre-commit` to the `[dev]` extra in `pyproject.toml` if it is not already there, so `make lint` is runnable straight after the documented dev install.
4. Fix the one failing file: `pnpm --dir frontend format` over `frontend/src/lib/forms/ticketForm.test.ts`, formatting only — no behaviour change in the test.
5. Remove the stale HANDOFF NOTE at the top of `.pre-commit-config.yaml`. It instructs a maintainer to `git mv .pre-commit-config.yaml.proposed .pre-commit-config.yaml`; that move has already happened and the file is at its real name. A note describing a state that no longer exists is the same class of defect as the rest of this milestone — a document asserting a layout that is not on disk.
6. Note in the Makefile why `lint` delegates rather than enumerates, so the next person adding a check adds it to `.pre-commit-config.yaml` and gets it in both places.

## Critical files

- `Makefile`
- `.pre-commit-config.yaml`
- `frontend/src/lib/forms/ticketForm.test.ts`
- `pyproject.toml`

## Interface & data

`make lint` → `pre-commit run --all-files`, exit non-zero on any hook failure, with a clear message when `pre-commit` is absent. No API, no schema, no runtime change. `make test`, `make build`, `make smoke` untouched. The hook set stays defined solely in `.pre-commit-config.yaml`.

## Verification

`make lint` is **green on the resulting branch** and red on the branch with the formatting fix reverted — the second half is the mutation, and without it the ticket only shows a passing command, not a working gate. `pre-commit run --all-files` and `make lint` produce the same verdict on: the current `main` (both red — today `make lint` is red for the fixture and CI is red for prettier, so agreeing at all is the change), the fixed branch (both green), and a branch with one deliberately unformatted frontend file (**both red** — this is the case `make lint` passes today and is the whole point). Confirm `tests/fixtures/` is still untouched by any hook, so the deliberately-malformed fixtures survive a run — a gate that "fixes" its own test data is a regression, not a pass. CI green on the PR.
