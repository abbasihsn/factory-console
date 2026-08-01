# [T91] Watch the JSON run-state source, so live updates are not silently dead

milestone: v2.1 · track: backend · depends_on: T78 · provides: `RealFileWatcher` observes the resolved run-state source whatever its kind, so a run-state change on a JSON-sourced project fires an SSE event instead of nothing.

## Context

**This is an audit-fix Ticket.** It comes from the program's first Incremental Integration Audit
(2026-08-01, DL-061), finding **F1**, `end-to-end-behaviour`, **blocking**, spanning **T40 + T78**.

**Neither ticket was wrong.** T40's watcher was correct when it was written; T78's JSON source was
correct in isolation. The defect exists only in their interaction, which is why no single Ticket's
review could have caught it and why the audit exists.

### The mechanism, verified against source

`RealFileWatcher` schedules its observers over `RUN_STATE_RELATIVE_LOCATIONS`, which is literally a
filter for directories:

```python
RUN_STATE_RELATIVE_LOCATIONS: tuple[Path, ...] = tuple(
    relative for kind, relative in RUN_STATE_SOURCE_LOCATIONS if kind == "directory")
```
`file_adapter/run_state.py:80`

T78 made `.factory/run-state.json` — a **file** — the *primary* source. It is not in that tuple, so it
is never scheduled. On a JSON-sourced project:

```
factory writes .factory/run-state.json  ->  no watchdog event
                                        ->  no ChangeEvent
                                        ->  no SSE
                                        ->  the live-update path is silently dead
```

**Silently** is the operative word. Nothing errors, nothing logs, and the UI simply never updates —
the failure mode that looks exactly like "nothing has changed yet".

## Staged approach

1. Derive the watched paths from the **resolved source**, not from the directory-only subset. A file
   source is watched by scheduling its **parent directory** and filtering events to that filename —
   watchdog cannot watch a single file that may be replaced by an atomic rename, and the factory
   writes `run-state.json` via `mktemp` + `mv` (INV-03), so a naive file watch would see the rename
   and stop observing the new inode.
2. Keep the directory form working unchanged. Both source kinds must fire, and the existing
   `is_run_state_marker` scope tagging must keep classifying directory events exactly as it does now.
3. Tag a JSON-source event with the same `ChangeScope` a directory-source event gets, so a subscriber
   cannot tell which source kind produced it. The scope describes what changed, not how it was stored.
4. **Do not widen what is watched.** `docs/planning/**` stays as it is; this ticket adds the run-state
   file and nothing else. An audit-fix Ticket that grows into a refactor is the *"broad collection of
   unrelated changes"* §10.3 forbids by name.

## Critical files

- `server/factory_console/file_adapter/watcher_real.py`
- `server/factory_console/file_adapter/run_state.py`

## Interface & data

No API change, no schema change. `RUN_STATE_RELATIVE_LOCATIONS` may stay as the directory subset if a
separate accessor supplies the file locations — the constant's current meaning is *"directory
locations"* and silently widening it would break `is_run_state_marker`, which reads it for scope
tagging on a path prefix.

## Verification

**The regression guard is an end-to-end assertion, not a unit test of the path list** — a test that
only checks the tuple's contents would pass against a watcher that still never schedules them.

- a project with `.factory/run-state.json` only: write the file, assert a `ChangeEvent` is delivered
  to a subscriber. **This is the test that fails on today's code**, and the reason this ticket exists;
- the atomic-rename case: replace `run-state.json` via `mktemp` + `mv` (what the factory actually
  does) and assert the event still fires — a watch that breaks on rename is the subtle way this fix
  fails while looking correct;
- a project with the run-state **directory** only: unchanged behaviour, event still fires, scope tag
  identical to today;
- a project with **both**: no duplicate events for one logical change;
- a project with **neither**: the watcher starts and has nothing scheduled, as now;
- the read-only guard test still passes — the watcher writes nothing.

`make lint`, `pytest`, `pnpm check`, `pnpm test` green.
