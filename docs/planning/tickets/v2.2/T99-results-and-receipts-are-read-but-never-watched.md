# [T99] The result and receipt artifacts are read but never watched

milestone: v2.2 · track: backend · depends_on: T90 · source: Incremental Integration Audit run 3 (2026-08-05), lens `end-to-end-behaviour`, tickets T83,T88,T90

## Context

`/runs` is the one view in the console that can never live-update.

Every other view refreshes the same way: the watcher notices a file change, pushes an SSE bump, and
`frontend/src/routes/+layout.svelte:25` calls `invalidateAll()`. That is the **only** refresh path in
the app. So a view refreshes exactly when the watcher is watching the files that view reads — and the
watcher is not watching these.

`runs.py:92-94` declares `RESULTS_RELATIVE_DIR`, `RECEIPTS_RELATIVE_DIR` and
`LAST_STOP_RELATIVE_PATH`. `watcher_real.py:227-262` schedules `docs/planning` recursively, plus
`RUN_STATE_RELATIVE_LOCATIONS` and the JSON sources' parents non-recursively — and then
`watcher_real.py:130-133` **drops every event under a json-only root that is not `run-state.json`
itself**. A result file landing in `.factory/results/` therefore produces no event at either stage.
`/runs` can only refresh when something *else* changes: run-state, or a planning file.

The practical shape: a lane finishes, writes its result and its receipt, and the operator watching
`/runs` sees nothing until an unrelated write happens to bump them. There is no error and no stale
indicator — the page simply keeps showing the previous answer, which is the failure mode a live view
exists to prevent.

**This is the third instance of one class.** T91 fixed it for one artifact. T95 filed acceptance
criterion 4 specifically to prevent its recurrence, and scoped that criterion to the **ledger**.
Results and receipts are a third pair of read-but-unwatched paths, and they were added *after* the
criterion was written — so the criterion did not fail, it was simply not about them.

That is the finding under the finding, and it belongs to whoever builds this: **a per-artifact
criterion cannot close a per-artifact class.** Each new reader adds a path, and nothing connects
"this module reads a factory artifact" to "the watcher must schedule it." The check that would close
it is structural — derive the watched set from the declared read set, or assert their equality — not
another criterion naming another file.

**Found by the audit, not by a review.** It takes T83, T88 and T90 together to state: T88 declared
the paths, T90 served them, T83 rendered them, and each one is correct alone. No single Ticket's own
review could have seen it, which is the test §10.2 uses for whether a finding is the audit's at all.

**Non-blocking, and that is the audit's verdict rather than a convenience.** Run 3 returned
`blocking: 0` and the loop decided `stop`, so v2.1's Version is not gated on this.

## Acceptance criteria

1. A change to a result or receipt artifact reaches `/runs` without a page reload. Asserted
   end-to-end — write a file, observe the bump — not by asserting the watcher's configuration, which
   is the thing that has now been wrong three times.
2. The `json-only root` filter at `watcher_real.py:130-133` no longer silently discards events for
   declared artifact paths. If a path is scheduled, an event under it either reaches a handler or is
   dropped for a **named** reason.
3. THE CLASS, not the instance: the set of paths the watcher schedules is **derived from or checked
   against** the set of artifact paths the file adapter declares. A test that fails when a module
   declares a new artifact path and nothing schedules it. A criterion naming
   `RESULTS_RELATIVE_DIR` and `RECEIPTS_RELATIVE_DIR` by hand is exactly what T95 already did, and
   it is why this Ticket exists.
4. T95's criterion 4 still passes, and its scope is not narrowed by this work.
