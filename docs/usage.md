# Usage

## Install

Factory Console ships as a Python wheel on PyPI. Run it without installing, or
install it onto your PATH with pipx:

```
uvx factory-console            # run without installing
pipx install factory-console   # install onto your PATH
```

## Run

From any App Factory project directory:

```
cd my-factory-project && factory-console
```

The console discovers the project (walking up from the current directory for
`docs/planning/tickets.json`), starts a local server on `127.0.0.1`, and logs the
URL Uvicorn is serving. Open that URL in your browser and press Ctrl-C to stop.

> **Walking skeleton (MVP).** Automatic browser opening, honoring an explicit
> `PATH`, port-in-use handling, and the richer exit codes below arrive with the
> full CLI wiring in backend T25. Today the CLI always serves the project
> discovered from the current directory and leaves `PATH`/`--no-browser` as
> accepted-but-unused stubs.

## Flags

```
factory-console [PATH] [--port N] [--host 127.0.0.1] [--no-browser] [--log-level LEVEL] [--version]
```

- `PATH` — the project directory to serve. _Accepted but not yet wired (T25):_
  discovery currently always walks up from the current directory.
- `--port N` — port to bind (`0` picks a free port). Port-in-use handling (a clean
  exit `2`) lands in T25; today an unavailable port surfaces as an unhandled
  Uvicorn error.
- `--host 127.0.0.1` — bind address; restricted to loopback (`127.0.0.1`,
  `localhost`, `::1`). A non-loopback host is rejected with exit `2`.
- `--no-browser` — _accepted but not yet wired (T25):_ no browser is opened yet
  regardless of this flag.
- `--log-level LEVEL` — logging verbosity (e.g. `info`, `debug`); logs go to
  stderr. An unrecognized level is rejected with exit `2`.
- `--version` — print the version and exit `0`.

**Path resolution (planned, T25):** an explicit `PATH` argument will win; until
then the CLI always walks up from the current directory looking for
`docs/planning/tickets.json`, the same way `git` finds its repo root. For the
authoritative contract see the CLI section of
[`planning/ARCHITECTURE.md`](planning/ARCHITECTURE.md).

## Exit codes

| Code | Meaning |
|---|---|
| `0` | ok (`--version`, or a clean run) |
| `2` | invalid `--host` (non-loopback) or unrecognized `--log-level` |

The richer, purpose-specific codes (`1` project-not-found, port-in-use,
malformed-manifest) arrive with the full CLI wiring in backend T25.

## What you'll see

_TODO (T34): browser screenshots of the ticket list, ticket detail, and
dependency-neighborhood views, captured by the Playwright screenshots pipeline._
Until that lands, run `factory-console` in a factory project to see the live UI.
