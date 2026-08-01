# tests/fixtures/runs/

The three run artifacts the T81 `GET /api/v1/runs` endpoint reads beside
`.factory/run-state.json`: a lane result, a review receipt, and the last-stop
file. Tests that need them at a real *location* copy them under `tmp_path` as
`.factory/results/<ID>.json`, `.factory/receipts/<ID>.json` and
`.factory/last-stop.json`.

## Provenance — read this before adding a field

`tests/fixtures/run_state/run-state.json` next door is a copy of the shape the
factory demonstrably writes. **These three are not**, and the difference is
deliberate rather than sloppy:

- `.factory/` is gitignored, so results, receipts and `last-stop.json` exist only
  on the host that actually ran the factory. No such file was reachable from the
  sandboxed worktree this was built in, and `docs/planning/ARCHITECTURE.md`
  documents no schema for any of the three (it covers only the run-state
  directory). So there was nothing to copy.
- `lane-result.json` is therefore written from the App Factory's
  `===LANE_RESULT===` block for `.factory/results/<ID>.json` — the block the
  team-lead lane emits, whose keys are `id`, `status`, `pr_url`, `route`,
  `review_iterations`, `verdict`, `built`, `review_summary`, `unresolved`,
  `handoff`, `worktree`, `spend`. Every key here comes from that list and no key
  was invented. Like the run-state fixture, it is written from the FACTORY's
  format rather than from what the console's parser happens to accept, and it
  carries keys the console deliberately does NOT model (`built`,
  `review_summary`, `unresolved`, `handoff`, `spend`, and `worktree` — an
  absolute host path the endpoint must never surface), so a parser that quietly
  requires a narrower shape, or that leaks the worktree path, fails here.

  **But be clear about what this fixture does and does not prove.** That key list
  lives in the factory's own source, not in this repository — grep for
  `LANE_RESULT` here and the only hits are T81's own files. So the fixture and the
  parser it exercises were derived from the SAME unverified assumption, and a test
  asserting one against the other cannot detect that the assumption is wrong.
  T81's Verification section asks for a real file precisely to break that circle;
  it has not been broken. **This is an open gap, not a solved one** — when a real
  `.factory/results/<ID>.json` becomes reachable, replace this file with it and
  re-check every modelled field.
- `last-stop.json` has no factory-side contract this repo can point at at all, so
  the console models exactly one field — `reason`, from the one-line description
  in `docs/planning/v2.1-PLAN.md` — and this fixture carries two extra keys
  (`at`, `sprint`) that must be ignored rather than rejected.
- `receipt.json` exists only so a receipt can be PRESENT. The console reads
  receipts for presence and never parses their content, so this file's fields are
  illustrative and nothing asserts against them.

**Read-only** — tests may read these but must never mutate them.
