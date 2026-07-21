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

The console discovers the project, starts a local server on `127.0.0.1`, prints
the URL, and opens your browser. Press Ctrl-C to stop it.

## Flags

```
factory-console [PATH] [--port N] [--host 127.0.0.1] [--no-browser] [--log-level LEVEL] [--version]
```

- `PATH` — the project directory to serve. Optional; see path resolution below.
- `--port N` — port to bind. If the chosen port is already in use the CLI exits `2`.
- `--host 127.0.0.1` — bind address; restricted to loopback (`127.0.0.1`,
  `localhost`, `::1`).
- `--no-browser` — start the server but do not open a browser tab.
- `--log-level LEVEL` — logging verbosity (e.g. `info`, `debug`); logs go to stderr.
- `--version` — print the version and exit.

**Path resolution:** an explicit `PATH` argument always wins. Otherwise the CLI
walks up from the current directory looking for `docs/planning/tickets.json`, the
same way `git` finds its repo root. For the authoritative contract see the CLI
section of [`planning/ARCHITECTURE.md`](planning/ARCHITECTURE.md).

## Exit codes

| Code | Meaning |
|---|---|
| `0` | ok |
| `1` | project-not-found |
| `2` | port-in-use |
| `3` | malformed manifest |

## What you'll see

_TODO (T34): browser screenshots of the ticket list, ticket detail, and
dependency-neighborhood views, captured by the Playwright screenshots pipeline._
Until that lands, run `factory-console` in a factory project to see the live UI.
