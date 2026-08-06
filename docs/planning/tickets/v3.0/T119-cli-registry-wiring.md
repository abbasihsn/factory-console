# [T119] CLI + dev app wire the SQLite registry and the watcher factory

milestone: v3.0 · track: backend · depends_on: T113, T114, T108, T25 · provides: the shipped `factory-console PATH` boot becomes multi-project-capable — registry wired, watcher factory injected, the discovered root pinned as an ephemeral session project — with the one-command invocation and all four exit codes unchanged.

## Context

The ticket that turns the milestone on, and the one where the CLI compatibility constraint is
settled.

`cli.py` is a Typer app with exactly ONE command. Adding a second would make Typer require a command
name, so `factory-console PATH` would break — the single most-used invocation in the project.
Therefore **v3.0 adds no subcommand and no new positional form**: same argument, same options, same
exit codes (`0` ok · `1` project-not-found · `2` port/host/log-level/token · `3` malformed
manifest), same stdout contract line, same browser open, same cheap-input-first ordering.
`factory-console serve` — the pathless long-running mode — is v3.1 and is where a second command
legitimately arrives.

**A consequence worth stating plainly:** in v3.0 you still boot FROM a project, and a pathless boot in
a non-project directory still exits 1 even if the registry holds projects. That is the conservative
reading of the CLI contract, and v3.1's `serve` is the fix.

The discovered root is pinned as an **ephemeral, UNREGISTERED session project** (T111's
`SESSION_PROJECT_ID`) rather than auto-registered. Auto-registration would make a read-only viewing
invocation silently write to the user's console db, and every throwaway clone, CI job and Playwright
boot would permanently grow the dropdown. Registering is an explicit `POST /api/v1/projects` the SPA
offers as a button (T124).

The registry the CLI opens must be redirectable so tests never touch the developer's real
`~/.factory-console/console.db`. That knob is `FACTORY_CONSOLE_DB_PATH` (T104); this ticket only
CONSUMES it. **The harness that sets it is T120 — do not duplicate it here.**

## Staged approach

1. EDIT `server/factory_console/cli.py`:
   - Import the concrete `SqliteProjectRegistry` alongside the other concretes this module is already
     the sole production constructor of (the ownership table permits concretes here).
   - Construct it AFTER the existing cheap-input validation and AFTER `discover_project`, so a bad
     host/port/log-level/token still exits 2 and a missing project still exits 1 before any db file
     is created. (Construction is side-effect-free per T108, so this ordering costs nothing; it is
     about where a failure would surface.)
   - **A registry that cannot be opened must NOT take the local viewer down**: log a warning to
     stderr and pass `project_registry=None`, which lands the app in pinned mode — exactly today's
     behaviour. The single-project viewer never needed a database and must not start needing one.
   - Pass `project_registry=...` and `watcher_factory=RealFileWatcher` to `create_app`, keeping the
     existing `file_watcher=RealFileWatcher(root)` initial so the pinned boot's watcher is live
     before the first request.
   - Extend the module docstring with the no-subcommand decision and the ephemeral-pin decision.
2. EDIT `server/factory_console/app.py` — `create_dev_app` gets the same lazy-imported registry +
   `watcher_factory`, so `scripts/dev.sh` exercises multi-project.
3. CREATE `tests/fixtures/projects/second/` — a second minimal App Factory project (its own
   `docs/planning/tickets.json` + one ticket `.md`) so a switch test has somewhere to switch TO. Add
   it to `tests/fixtures/projects/README.md` (fixtures are executable docs of supported shapes).
4. EDIT `tests/integration/test_cli.py` — the existing subprocess launch still prints the exact
   contract line and exits 0 on SIGINT; a run pointed at an unwritable db directory still boots
   (pinned, warning on stderr); `/api/v1/projects` on a freshly booted CLI shows exactly the
   `session` row.
5. EDIT `docs/usage.md` — a short "Multiple projects" section: register with the SPA button, switch
   with the dropdown, what happens with no selection, and that `serve` is v3.1.

## Critical files

- `server/factory_console/cli.py` (modify — aggregation file)
- `server/factory_console/app.py` (modify — aggregation file)
- `tests/integration/test_cli.py` (modify)
- `tests/fixtures/projects/second/docs/planning/tickets.json` (create)
- `docs/usage.md` (modify)

## Interface & data

**CLI contract (ARCHITECTURE.md → CLI contract) — unchanged, verbatim:**
`factory-console [PATH] [--port N] [--host 127.0.0.1] [--no-browser] [--log-level LEVEL]
[--version]`; exit codes `0`/`1`/`2`/`3`; stdout
`Factory Console vX.Y.Z — serving <root> at http://127.0.0.1:<port>`. No new flag, no subcommand.

Wiring: `create_app(..., project_registry=SqliteProjectRegistry(<db path from
FACTORY_CONSOLE_DB_PATH or the default>), watcher_factory=RealFileWatcher,
file_watcher=RealFileWatcher(root), project_root=root)`.

Contracts by reference: `ProjectRegistry` + `SqliteProjectRegistry` + `resolve_db_path()` (T104,
T106, T108); `FileWatcher` (T39); `read_write_token()` / `require_loopback_host()` (`config.py`) —
both unchanged, and **the loopback validator is deliberately NOT relaxed** (that is v3.1).

DB ops: opening (and letting T105's code migrate) the console db at first registry use; no schema is
defined here. A failure to open degrades to pinned mode. NFR flags: boot-time db work happens before
uvicorn serves, so no event-loop concern; loopback bind unchanged; the write token is minted exactly
as today.

## Verification

`python -m pytest tests/integration/test_cli.py -q`, then `python -m pytest -q`. `make lint`.
**`make smoke`** (packages the wheel and boots the installed CLI) — the critical regression check
that `factory-console PATH` is unchanged end to end. Manual: run
`factory-console tests/fixtures/projects/minimal`, confirm the exact stdout line, then
`curl -s localhost:PORT/api/v1/projects` shows the single `session` row.
