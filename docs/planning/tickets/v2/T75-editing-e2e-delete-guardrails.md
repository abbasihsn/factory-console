# [T75] Editing e2e (part 2): delete-with-confirm + non-todo disabled state + banner spec

milestone: v2 · track: testing · depends_on: T65, T70, T74 · provides: Playwright coverage that a todo ticket can be deleted with a confirm step, and that a non-todo ticket shows the disabled edit state + explanatory banner (edit/save controls unavailable).

## Context

The other half of the editing acceptance surface is the guardrails the user SEES: destructive deletes require an explicit confirm, and non-editable (non-todo) tickets must visibly disable editing behind a banner explaining why. Splitting these out of part 1 keeps each PR simple while covering the negative/guardrail flows that most protect the immutability contract in the UI.

## Staged approach

1. Create `frontend/tests/e2e/editing-guardrails.spec.ts`, importing the extended `start()`/`DedicatedConsole` from `./lib/dedicated-console` (IMPORT ONLY — do not modify the harness here, so it does not collide with T74).
2. Delete-with-confirm: open a todo ticket, trigger delete, assert the confirm affordance appears, confirm, and assert the ticket disappears from the list/detail; optionally assert cancel leaves it intact.
3. Non-todo disabled state: navigate to a non-todo ticket (the `with_run_state` fixture has ready/in-flight/merged tickets, e.g. CAD-118 ready, CAD-100 merged), assert the edit control is disabled and the explanatory banner is visible; assert attempting to edit is not possible.
4. Reuse the beforeAll/afterAll(dispose) guarded-handle pattern; keep to two concise specs.

## Critical files

- `frontend/tests/e2e/editing-guardrails.spec.ts` (new)

## Interface & data

UI under test (by reference): the v2 delete-with-confirm dialog and the non-todo disabled-edit banner from T70. Fixtures: `tests/fixtures/projects/with_run_state` (CAD-140 todo, CAD-118 ready, CAD-100 merged, etc.) via the harness copy. Endpoints exercised: `DELETE /api/v1/tickets/{id}` with the `X-Factory-Write-Token` header (T65). Entities: `RunState` enum (drives the disabled state). No DB — only the disposable temp copy. NFR: run-state authorization surfaced in UI, destructive-op confirmation.

## Verification

`pnpm --dir frontend e2e` (or target `editing-guardrails.spec.ts`) after `playwright install`. Uses the harness's isolated temp copy; shared fixtures untouched.
