# tests/fixtures/run_state/

`run-state.json` is a fixture in the shape the **App Factory itself writes** at
`<project>/.factory/run-state.json` — `{"version": int, "tickets": {ID:
{"status": str, "pr_url": str|null}}, "parts_landed": object}`, with `status`
drawn from the factory's nine `FAC_STATES`
(`todo in_progress ready in_part in_submilestone merged flagged failed
needs_human`). The live file is gitignored (`.factory/`), so this copy carries
the same structure with the ids and PR urls of this repository's own run.

It is written from the FACTORY's format, deliberately NOT from what the console's
parser happens to accept: a fixture built from the same assumption as the code
under test cannot detect that the assumption is wrong, which is exactly how the
console shipped a marker-directory reader for a file the factory never writes.
It covers all nine statuses, both `pr_url` forms (a string and `null`), and the
`parts_landed` key the console does not read — so a parser that quietly requires
a shape the factory does not produce fails here.

**Read-only** — tests may read it but must never mutate it. Tests that need it at
a run-state *location* copy it under `tmp_path`.
