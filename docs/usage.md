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
URL Uvicorn is serving. Your browser opens on that URL automatically unless you pass
`--no-browser`; press Ctrl-C to stop.

## Flags

```
factory-console [PATH] [--port N] [--host 127.0.0.1] [--no-browser] [--log-level LEVEL] [--version]
```

- `PATH` — the project directory to serve. A directory that holds no
  `docs/planning/tickets.json` is rejected with exit `1`.
- `--port N` — port to bind (`0` picks a free port). A port already in use is
  rejected with a clean exit `2`.
- `--host 127.0.0.1` — bind address; restricted to loopback (`127.0.0.1`,
  `localhost`, `::1`). A non-loopback host is rejected with exit `2`.
- `--no-browser` — don't open the browser on startup.
- `--log-level LEVEL` — logging verbosity (e.g. `info`, `debug`); logs go to
  stderr. An unrecognized level is rejected with exit `2`.
- `--version` — print the version and exit `0`.

**Path resolution:** an explicit `PATH` argument wins; without one the CLI walks up
from the current directory looking for `docs/planning/tickets.json`, the same way
`git` finds its repo root. For the authoritative contract see the CLI section of
[`planning/ARCHITECTURE.md`](planning/ARCHITECTURE.md).

## Environment

- `FACTORY_CONSOLE_HOST` / `FACTORY_CONSOLE_PORT` / `FACTORY_CONSOLE_LOG_LEVEL` — env
  equivalents of the flags above. An explicit flag wins over the env var, and the env
  var over the default; all three run through the same validation either way.
- `FACTORY_CONSOLE_WRITE_TOKEN` — a **development and testing** override that pins the
  write token below to a fixed value instead of minting a fresh one. Normal runs leave
  it unset; the token is per-session by design. If you do set it, it must be at least 16
  characters — a blank or too-short value is rejected with exit `2` rather than silently
  falling back to a generated token.

### The write token

The console mints a write token at every start and prints it to **stderr**, so you'll
see a line like this in the output of any run:

```
X-Factory-Write-Token: 3s9Kv-1QpZ...
```

That token authorizes write requests, sent in the `X-Factory-Write-Token` header. It
lasts only as long as the process, so the one printed by a previous run stops working.
Reads never need it, so browsing the project is unaffected; the three write endpoints
require it on every request:

| Endpoint                      | Effect                                         |
| ----------------------------- | ---------------------------------------------- |
| `POST /api/v1/tickets`        | create a ticket (`201`, or `200` on a dry-run) |
| `PUT /api/v1/tickets/{id}`    | edit a ticket (`200`)                          |
| `DELETE /api/v1/tickets/{id}` | delete a ticket (`200`)                        |

Each returns the same `WriteResult` body carrying the unified diff of what changed, and
each accepts `?dryRun=true` to get that diff back **without** writing anything. `dryRun`
is the only query parameter these endpoints take, and it is matched exactly: any other
key — including a miscasing like `?dryrun=true` — is refused with
`400 unknown_query_param` rather than quietly applying the write you meant to preview.
Sending `dryRun` **more than once** (`?dryRun=true&dryRun=false`) is refused the same way
with `400 repeated_query_param`, because only the last value would otherwise bind — so a
request that asks for a preview must never apply.

Only tickets whose factory run-state is `todo` (or `unknown`, when the project has no
run-state directory) may be edited or deleted — an `in-flight`, `ready`, or `merged`
ticket is refused with `409 ticket_not_mutable`. That gate guards the **write**, so it
fires on an apply; `?dryRun=true` still returns the preview diff for such a ticket, and
the refusal comes when you apply it. Creating an id that already exists, by contrast, is
refused with `409 write_conflict` on **both** paths, because previewing a create that
could never succeed would be a misleading preview. A missing or wrong token is
`401 write_token_invalid`, and an id outside the ticket-id pattern is
`400 invalid_ticket_id`.

The console binds to loopback only, so the token is defence-in-depth *behind* that
boundary — it stops another process on your machine, or a drive-by request from a page in
your browser, from mutating the project. There is no command-line flag
for it, because anything on the command line is readable by every local process.

If you pinned the token with the dev override above, the value is *not* echoed — you
already have it, and printing it would copy it into whatever captures stderr — so the
line reads `X-Factory-Write-Token: <pinned, not echoed>`.

## Exit codes

| Code | Meaning                                                       |
| ---- | ------------------------------------------------------------- |
| `0`  | ok (`--version`, or a clean run)                              |
| `1`  | project not found                                             |
| `2`  | invalid `--host` (non-loopback), out-of-range `--port`, unrecognized `--log-level`, a bad `FACTORY_CONSOLE_WRITE_TOKEN` pin, or the port already in use |
| `3`  | malformed ticket manifest                                     |

## What you'll see

Every page shares a header with a **Factory Console** label, the served project
path, a navigation cluster (**Home / Graph / Roadmap** links plus a **global
search box**), and a **Reload** button. A **live-update indicator** pill sits just
below the header on the right.

### Ticket list, detail, and deps

The landing page (`/`) is a searchable, filterable list of every ticket. Open a
ticket for its detail view — the rendered `.md` body, resolved
`depends_on` / `provides`, and a factory run-state badge — and follow "View dep
neighborhood" for that ticket's direct deps and dependents as clickable links.

### Global search

The search box in the header is a **full-text** search over a ticket's `id`,
title, `provides`, _and_ body Markdown. Type a term and press Enter to land on
`/search`, which lists the matching tickets (each row showing which fields
matched, e.g. `bodyMarkdown`); every result
links through to its ticket detail. An empty or no-match query renders a friendly
empty state rather than an error.

### Dependency graph

`/graph` (the header **Graph** link) draws the whole project as a
dependency DAG. Each node is a ticket colored by its factory run-state
(`todo` / `in-flight` / `ready` / `merged`), and edges point from a ticket to the
tickets it depends on. Click a node to open that ticket's detail page.

### Roadmap

`/roadmap` (the header **Roadmap** link) renders the project's `ROADMAP.md` as
milestone sections — each item shows its checkbox state and, when it references a
ticket, a monospace id link into the ticket detail — followed by the roadmap's
prose body.

### Live updates

The console watches the project on disk. When its tickets change — a run-state
marker moves, or a ticket's `.md` is edited — an open page **auto-refreshes**
over a Server-Sent-Events stream — no manual reload needed. The indicator pill reflects the stream's health: **Connecting…** while it
opens, **Live** once connected, **Offline** if the stream drops, and it briefly
flashes **Updated** when a change arrives. Where the browser has no `EventSource`,
the app degrades gracefully to the manual **Reload** button.

### Screenshots

Captured from the real UI by the Playwright screenshots pipeline against the
`with_run_state` fixture — see the ["Screenshots"](../README.md#screenshots)
section of the root README to regenerate them.

![Ticket list](screenshots/list.png)

_The searchable ticket list at `/`._

![Ticket detail](screenshots/detail.png)

_The `CAD-125` detail view with rendered body, deps, and run-state badge._

![Dependency neighborhood](screenshots/deps.png)

_The `CAD-125` dependency neighborhood listing its direct deps._

![Global search results](screenshots/search.png)

_Full-text search for `idempotent` at `/search`, matching two ticket bodies._

![Dependency graph](screenshots/graph.png)

_The `/graph` dependency DAG, nodes colored by factory run-state._

![Roadmap](screenshots/roadmap.png)

_The `/roadmap` milestone view rendered from the project's `ROADMAP.md`._

![Live-update indicator](screenshots/live.png)

_The live-update pill in its connected `Live` state._
