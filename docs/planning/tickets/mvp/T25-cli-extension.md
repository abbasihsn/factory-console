# [T25] CLI extension: discovery + port + browser + signals + exit codes

milestone: MVP · track: backend · depends_on: T20, T21, T22, T23, T24, T11, T17 · provides: Real `factory-console` CLI per `ARCHITECTURE.md` CLI contract — the only place that constructs `RealFileAdapter` for production launches (dev launches use `create_dev_app` per T20)

## Context

Extends the T06 walking-skeleton `cli.py` into the real Typer entrypoint that ships in the wheel. This is the ONLY place in the codebase that constructs the concrete `RealFileAdapter` and passes it into `create_app` for production; `create_dev_app` (T20) uses it for the dev loop. Wires path discovery, port selection, Uvicorn boot bound to 127.0.0.1, browser open via `webbrowser`, SIGINT for clean shutdown, and exit codes `0/1/2/3`.

## Staged approach

1. Extend `cli.py` Typer app (`main` is already defined).
2. `--version` prints `'factory-console vX.Y.Z'` + `typer.Exit(0)`.
3. Path discovery via `from factory_console.file_adapter.discovery import discover_project`; `root = discover_project(path or Path.cwd())`; on `ProjectNotFound` raise `typer.Exit(1)` after stderr message.
4. Construct `file_adapter = RealFileAdapter()` (concrete import lives ONLY here + in `create_dev_app`); `app = create_app(file_adapter, version=__version__, project_root=root)`.
5. Validate `host` via `Settings.host` validator (do NOT bypass).
6. Port selection: if `--port 0`, bind an ephemeral socket first (`socket.socket + bind (host, 0) + getsockname`), close, pass that port to Uvicorn; on `OSError EADDRINUSE` for a user-specified `--port` print + `typer.Exit(2)`.
7. Print exact contract line `'Factory Console v{version} — serving {root} at http://{host}:{port}'` before starting Uvicorn.
8. If not `--no-browser`, `webbrowser.open` after server-ready (Uvicorn startup hook or small `threading.Timer`).
9. `SIGINT/SIGTERM` handler triggers `uvicorn.Server.should_exit = True`, exit 0.
10. `MalformedManifest` during initial load wrapped -> `typer.Exit(3)`.
11. `configure_logging(--log-level)`.
12. `tests/integration/test_cli.py`: subprocess launches `factory-console <fixture> --no-browser --port 0`, parses printed URL, `httpx.get /health` asserts `{ ok, projectRoot=<fixture> }`, SIGINT, assert `wait()` returns 0 within 3s; unknown path -> exit 1; malformed fixture -> exit 3; `--port` already-in-use -> exit 2; `--version` -> exit 0.

## Critical files

- `server/factory_console/cli.py`
- `server/factory_console/__init__.py`
- `tests/integration/test_cli.py`

## Interface & data

Implements full CLI contract (flags, exit codes, path-resolution, stdout URL line). Only place importing `RealFileAdapter + discover_project` concretely for production boot.

## Verification

pytest `test_cli.py` green across all subprocess cases (macOS + Linux CI); manual: `factory-console <fixture> --no-browser --port 0` prints URL, serves `/health`, Ctrl-C exits 0; `factory-console /nonexistent` exits 1; `factory-console <malformed>` exits 3; `factory-console --version` exits 0.
