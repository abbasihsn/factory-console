# [T143] A multi-entry `provides` must survive an edit

milestone: v3.0.1 · track: fullstack · depends_on: T130 · provides: a `provides` write contract that round-trips every shape the read contract accepts, so editing an unrelated field cannot silently discard manifest data.

## Context

`Ticket.provides` is `list[str]` on the read side, and the manifest reader **deliberately**
supports a genuine multi-entry list:

```python
# file_adapter/manifest.py — _provides_to_list
if isinstance(value, list):
    return value          # an existing list passes through
```

The write side does not. `domain/write.py` types it `provides: str = ""` on both write DTOs, so
the generated client types it as a scalar, and the edit form holds it in a single-line input.
Editing **any** field on a ticket whose manifest carries a multi-entry `provides` therefore sends
one joined string back, and the entry is flattened to a single element on the next read.

This is the only finding on `docs/DEFERRED_FINDINGS.md` (as **D3**) that silently **corrupts user
data** rather than merely reading awkwardly. Nothing warns; the user edits a title and loses a
list. It stayed unfixed through v2.x and v3.0 because a real fix crosses the wire contract, the
generated client and the form — outside what a QA pass may change on a minimal diff.

`frontend/src/lib/forms/ticketForm.ts` already documents the hazard accurately and instructs
callers **not** to `parseList` it. That comment is correct today and must be updated by this
ticket rather than left to contradict the new contract.

## Staged approach

1. **Decide the write shape and write it down first.** Two defensible answers, and the choice
   belongs in `ARCHITECTURE.md` under Contracts → REST v1, not in a lane's head:
   - **`list[str]`, symmetric with the read model** — preferred. The read contract already
     accepts it; making the write contract match removes the asymmetry that caused the bug.
   - `str | list[str]` — narrower blast radius, but it keeps two shapes alive on one field and
     the collapse returns the moment someone forgets which they hold.

   Prefer the first unless a concrete consumer makes it impossible; record the reason either way.
2. **`server/factory_console/domain/write.py`** — change `provides` on both write DTOs
   (lines ~49 and ~68). Accept the legacy scalar on input and normalise it, so a client that
   predates this ticket is not broken by it.
3. **The writer** — ensure a multi-entry value serialises back to the manifest in the shape the
   reader passes through, and that a single-entry value still writes as a scalar. A ticket that
   never had a list must not acquire one just by being edited: that would churn every manifest
   entry it touches.
4. **Regenerate the typed client** (`frontend/src/lib/api/types.ts`). Note that codegen is a
   manual script with no CI gate — see the standing note about `projectRoot` in
   `DEFERRED_FINDINGS.md`, which is stale for the same reason.
5. **`frontend/src/lib/forms/ticketForm.ts`** — `provides` joins `dependsOn` and `files` as a
   newline-delimited textarea field using the existing `parseList` / `serializeList` pair, and
   the block comment at lines 33–38 is rewritten to state the new contract.
6. **`EditTicketModal.svelte`** — swap the single-line input for the textarea, matching the two
   fields beside it.
7. **Delete D3 from `docs/DEFERRED_FINDINGS.md`**, per that file's own instruction to remove an
   entry when it lands. **D4** (`toTicketUpdate` asserts a return type it cannot structurally
   guarantee) is the same root cause and should be resolved in this pass.

## Critical files

- `server/factory_console/domain/write.py`
- `server/factory_console/file_adapter/manifest.py` (reader — reference, likely unchanged)
- the manifest writer and its fake
- `frontend/src/lib/api/types.ts` (regenerated)
- `frontend/src/lib/forms/ticketForm.ts`
- `frontend/src/lib/components/EditTicketModal.svelte`
- `docs/planning/ARCHITECTURE.md`, `docs/DEFERRED_FINDINGS.md`

## Interface & data

The wire field changes shape. The read contract is unchanged — this ticket makes the **write**
contract accept what the read contract already emits. Round-tripping is the invariant: for every
`provides` the reader accepts, writing an unrelated field and reading back must yield the same
value.

## Verification

The discriminating test is a **round-trip through the real write path**, not a unit assertion on
either half — the halves are individually correct today and the bug lives in the seam:

1. Seed a manifest whose ticket has `"provides": ["a", "b"]`.
2. `GET` it, change **only** the title, `PUT` it back.
3. `GET` again and assert `provides == ["a", "b"]`.

That test must **fail on `main`** before the fix — confirm it does, or it is not testing this bug.

Also cover: a single-entry `provides` still writes as a scalar and does not churn the manifest;
an empty one stays empty; and a legacy scalar sent by an un-regenerated client is still accepted.

Then: `make lint`, `make test`, `pnpm --dir frontend test`.
