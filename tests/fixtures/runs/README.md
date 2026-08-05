# tests/fixtures/runs/

`result.json` and `receipt.json` are stand-ins for the two per-ticket artifacts
the App Factory writes beside `.factory/run-state.json` —
`.factory/results/<ticket_id>.json` and `.factory/receipts/<ticket_id>.json`.

**These are ILLUSTRATIVE EXAMPLE SHAPES, not verbatim captures from a real
factory run.** No `.factory/results/` or `.factory/receipts/` file was available
to copy when they were written (`.factory/` is gitignored, and this worktree has
none), so the field names here are plausible rather than authoritative — do not
read them as the factory's contract, and do not build a schema from them. That is
the opposite of `tests/fixtures/run_state/run-state.json`, which IS written from
the factory's real format, and the difference is stated here rather than glossed
over: a fixture that claims a provenance it does not have is worse than no
fixture, because it invites exactly the schema-from-guesswork these examples
cannot support.

What they are good for is the one thing T88's reader actually asks of them: each
is **a valid JSON object**, so a test can point `read_result`/`read_receipt` at
it and assert the successful-parse case against real bytes on disk. T88 reads
these artifacts as untyped `dict[str, Any]` and models no field, so the specific
keys are immaterial to what is under test.

T89 has since composed these reads into `domain/run_record.py`'s `RunRecord`, and
it deliberately did **not** model named fields: the record names the two *sources*
(`result`, `receipt`) and carries each `ArtifactRead` verbatim, reason and all,
while `data` stays `dict[str, Any]`. So the payload is still untyped, for the
reason stated above — no real captured artifact exists to verify field names
against, and a schema built from these illustrative shapes would silently reject
what the factory actually writes. Do not read `RunRecord` as the place a field
schema already lives.

**Read-only** — tests may read these but must never mutate them. Tests that need
one at an artifact *location* copy it under `tmp_path`.
