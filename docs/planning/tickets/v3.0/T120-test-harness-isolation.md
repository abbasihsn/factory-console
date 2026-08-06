# [T120] Test-harness isolation: no test may touch the developer's real console DB

milestone: v3.0 · track: testing · depends_on: T119, T104 · provides: every Playwright run AND every CLI-subprocess pytest run boots against a throwaway registry DB instead of the developer's (and CI's) real `~/.factory-console/console.db`, plus the `startMulti()` / `registerProject()` helpers a multi-project spec needs.

## Context

v3.0 gives the console its **first writable state outside a target project**, and the test suites
boot the REAL packaged CLI. As of T119 the shipped CLI wires the SQLite registry; from T122 the SPA's
layout load calls `/api/v1/projects/current` on every page, which is the first registry method call,
which per T108 is exactly what creates `~/.factory-console/console.db`. Without this ticket the
existing suite would quietly register fixture temp dirs into the developer's real registry and leave
them behind — and CI would do the same to its runner's home.

**This ticket therefore lands BEFORE any frontend work**, not alongside the multi-project spec that
happens to need its helpers. Ordering it later would leave five ticket boundaries writing into a real
home directory.

Its scope is wider than one file, because the pollution has three doors:
- `frontend/tests/e2e/global-setup.ts` spawns the packaged CLI with `env: process.env` and no
  isolation — this is the SHARED console every non-dedicated spec uses (happy-path, graph, search,
  editing, editing-guardrails, screenshots);
- `frontend/tests/e2e/lib/dedicated-console.ts` does the same for the specs that own their console;
- `tests/integration/test_cli.py` subprocess-launches the CLI with the ambient environment.

The second half is the prerequisite a multi-project spec needs: a way to get more than one project
registered, which the harness does the way a user does — through the API, with the write token it
already parses off the console's stderr.

## Staged approach

1. EDIT `frontend/tests/e2e/global-setup.ts`: create a per-run temp directory and pass
   `FACTORY_CONSOLE_DB_PATH` (pointing at a file inside it) into the spawned child's env, alongside
   the existing `process.env`. Write the temp path where teardown can find it, next to the existing
   `PID_FILE` convention.
2. EDIT `frontend/tests/e2e/global-teardown.ts`: remove that temp directory after killing the child,
   with the same swallow-ENOENT hygiene the file already uses. **Assert that `~/.factory-console/`
   was not created** during the run (or, if it already existed, that its mtime is unchanged).
3. EDIT `frontend/tests/e2e/lib/dedicated-console.ts`: apply the same isolation (its own temp db,
   removed in `dispose`), and export:
   - `registerProject(handle, fixtureName)` — copies a fixture into its own temp dir, POSTs it to
     `/api/v1/projects` with the handle's `writeToken`, returns `{ id, name, path }`;
   - `startMulti(fixtures: string[])` — boots on the first and registers the rest.
   Extend `dispose` to remove every temp dir it created, keeping the existing "never leak a child or
   a temp dir on a failed start" discipline.
4. Document at the top of the helper WHY registration goes through the API rather than a CLI
   subcommand: `cli.py` is a single-command Typer app and adding a second command changes how
   `factory-console PATH` must be invoked (T119) — a compatibility constraint the harness has no
   business forcing.
5. ADD a pytest fixture — autouse, in the integration `conftest.py` — that points
   `FACTORY_CONSOLE_DB_PATH` at `tmp_path` for every test, so `test_cli.py`'s subprocess launches and
   any future in-process registry use are isolated by default rather than by remembering.
6. Note for consumers: `GET /api/v1/health`'s `projectRoot` is nullable as of T116 — update any
   harness assertion that treated it as a string.

## Critical files

- `frontend/tests/e2e/global-setup.ts` (modify)
- `frontend/tests/e2e/global-teardown.ts` (modify)
- `frontend/tests/e2e/lib/dedicated-console.ts` (modify)
- `tests/integration/conftest.py` (create or modify — autouse isolation fixture)

## Interface & data

`startMulti(fixtures: string[]): Promise<DedicatedConsole>` and
`registerProject(handle: DedicatedConsole, fixtureName: string): Promise<{ id, name, path }>`;
`DedicatedConsole` gains the registered projects it created.

Contracts by reference: the CLI contract (`factory-console [PATH] --no-browser --port 0`, its one
stdout URL line and its stderr write-token line — both already parsed here); the
`POST /api/v1/projects` endpoint (T113) and the `X-Factory-Write-Token` header;
`FACTORY_CONSOLE_DB_PATH` (T104).

DB ops: none directly — the harness only RELOCATES the console's SQLite file via the environment; it
never opens it. NFR flags: process + temp-dir hygiene (SIGTERM → poll → SIGKILL, unconditional
cleanup); **no test, at any layer, may touch `~/.factory-console/`**.

## Verification

`pnpm --dir frontend exec playwright test` — the whole existing suite must pass unchanged with the
isolation in place. Then confirm the protection actually holds: with no `~/.factory-console/console.db`
present before the run, assert none exists afterward (and, if one already exists, that its mtime is
unchanged). Same check around `python -m pytest tests/integration/test_cli.py -q`. Also
`pnpm --dir frontend lint` and `make lint`.
