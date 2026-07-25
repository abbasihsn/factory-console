# [T77] v2 docs refresh: usage.md editing section + README write capabilities + write-token workflow

milestone: v2 · track: docs · depends_on: T54, T65, T70, T71, T76 · provides: usage.md gains an Editing section (create/edit/delete todo tickets, diff-preview, non-todo restriction) and the loopback write-token workflow; README reflects the new write capabilities and verifiable signed releases.

## Context

v2 turns the console from read-only into a safe editor, so the user-facing docs must explain what can be edited (only todo/unknown tickets), how (edit form + diff-preview + confirm), and how writes are authorized (the per-session loopback write token printed to stderr and sent as a header). It should also point users at the new signed-release verification. This mirrors the T54 v1 docs refresh pattern and keeps docs in step with the shipped behavior.

## Staged approach

1. MODIFY `docs/usage.md`: add an 'Editing tickets' section covering the run-state gate (only todo/unknown are editable; in_flight/ready/merged stay read-only), the create/edit/delete flows, the diff-preview + confirm step, and the disabled-state banner for non-todo tickets.
2. Document the write-token workflow: the console prints a per-session loopback-only token to stderr at boot; the SPA prompts for it and sends it as the `X-Factory-Write-Token` header; note it is loopback-only and per-session (no persistence).
3. MODIFY `README.md`: update the feature list/intro from read-only to 'view + safely edit todo tickets', and add a short 'Verifying releases' note pointing at the sigstore/cosign attestations from T76.
4. If v1 established a screenshots pipeline (T54), add/reference an editing screenshot placeholder consistent with that flow (do not rebuild the pipeline). Keep prose additive and within a simple-PR budget.

## Critical files

- `docs/usage.md`
- `README.md`

## Interface & data

Documents (by reference, do not restate schemas): the run-state editability contract (RunState todo/unknown only), the write endpoints POST/PUT/DELETE /api/v1/tickets (T65), the per-session loopback write-token header `X-Factory-Write-Token` (auth model, T64), and the signed-release verification (T76). No DB. NFR: documents the auth (write-token) + run-state-authorization model for users.

## Verification

Docs/prettier lint if configured; render `docs/usage.md` + `README.md` locally and confirm the editing + write-token + release-verification sections read correctly and match the shipped v2 behavior. No code execution required.
