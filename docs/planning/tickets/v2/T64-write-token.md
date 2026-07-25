# [T64] Per-session loopback write token: generate at boot, print to stderr, enforce X-Factory-Write-Token

milestone: v2 · track: backend · depends_on: T20, T25, T62 · provides: app.state.write_token generated per session + printed to stderr; require_write_token dependency raising WriteTokenInvalid (401) on missing/mismatched X-Factory-Write-Token.

## Context

v2 writes cross a state-changing boundary, so — while the server stays loopback-only — a per-session secret gates every mutation, defending against other local processes / drive-by browser requests. The token is minted once per server start, surfaced to the human operator on stderr, and required as an `X-Factory-Write-Token` header on write routes only; read routes stay header-free so the SPA's viewing flows are unchanged. It threads through `config.py` (an optional override for tests/dev) and `create_app` (generation + stash + stderr print), and a dedicated verifier dependency the write router attaches.

## Staged approach

1. In `config.py`, add `write_token: str | None = None` to `Settings` (sourced from `FACTORY_CONSOLE_WRITE_TOKEN`) so tests/dev can pin a deterministic value; default `None` means auto-generate. Add a module constant `WRITE_TOKEN_HEADER = 'X-Factory-Write-Token'`.
2. CREATE `api/write_token.py`: `WriteTokenInvalid` (`FactoryConsoleError`, code `write_token_invalid`, status 401, generic message — no token echo) and `require_write_token(request: Request) -> None` that reads `request.app.state.write_token`, pulls the `X-Factory-Write-Token` header, and `secrets.compare_digest`-compares; raises `WriteTokenInvalid` on missing/empty/mismatch.
3. In `app.py` `create_app`, add a keyword-only `write_token: str | None = None`; resolve `token = write_token or secrets.token_urlsafe(32)`, stash on `app.state.write_token`, and print `X-Factory-Write-Token: <token>` (with a one-line hint) to `sys.stderr`. Wire `create_dev_app`/callers to pass through `Settings().write_token` when set.
4. Publish the security scheme in OpenAPI so the SPA regenerates it. Keep read routes untouched (no global dependency). Do not re-export from any `__init__`.

## Critical files

- `server/factory_console/config.py`
- `server/factory_console/app.py`
- `server/factory_console/api/write_token.py` (new)

## Interface & data

`require_write_token(request: Request) -> None` (FastAPI dependency, raises on failure); `create_app(..., write_token: str | None = None)`; `Settings.write_token: str | None`; `WRITE_TOKEN_HEADER = 'X-Factory-Write-Token'`. By reference: `FactoryConsoleError`/`to_error_response` envelope (`{error:{code,message}}`), rendered by the existing single handler at 401. No DB. NFR: AUTH SCOPE — write-only token, constant-time compare, no token in logs/response body; loopback trust boundary preserved; token regenerated each process start.

## Verification

`pytest tests/integration`: build the app with a pinned `write_token`; assert a write route with the correct header passes the dependency, missing/wrong header yields the `write_token_invalid` 401 envelope, and read routes ignore the header entirely. Unit-assert `create_app` auto-generates a token when none supplied and writes an `X-Factory-Write-Token:` line to stderr (capture via capsys). ruff clean.
