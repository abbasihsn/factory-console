# [T28] API client + generated TS types (openapi-typescript codegen)

milestone: MVP · track: frontend · depends_on: T27, T21 · provides: Typed same-origin API client with normalized `ApiError` + `pnpm codegen` script that regenerates `src/lib/api/types.ts` from `/api/v1/openapi.json`

## Context

Every other frontend ticket needs a typed way to talk to the server. Delivers `client.ts` (~60-line thin fetch wrapper) that returns typed responses and throws normalized `ApiError` on non-2xx/network failure, plus `openapi-typescript`-based codegen. `types.ts` is committed with a `'DO NOT EDIT — regenerate with pnpm codegen'` header banner.

## Staged approach

1. Add `openapi-typescript` to `frontend/package.json` devDeps + `codegen` script: `openapi-typescript ${FC_OPENAPI_URL:-http://127.0.0.1:8000/api/v1/openapi.json} -o src/lib/api/types.ts --immutable`.
2. `frontend/openapi-codegen.config.ts` documents URL/output/banner/immutability.
3. `frontend/scripts/postcodegen.mjs` prepends the banner header. Wire as post-codegen step in package.json script.
4. Run codegen once against running backend to produce `src/lib/api/types.ts`.
5. `src/lib/api/errors.ts`: `class ApiError extends Error { code: string; status: number; details?: unknown }`.
6. `src/lib/api/client.ts` exports: `getProject()`, `listTickets(params)`, `getTicket(id)`, `getTicketDeps(id)`, `getRoadmap()`, `getHealth()`. Private `request<T>(path, init?)` builds URL from `/api/v1/${path}`, calls `fetch`; non-2xx -> parses envelope -> throws `ApiError`; network failure -> throws `ApiError({ code: 'network_error' })`; else `await res.json() as T`.
7. Type every wrapper from `./types` (`TicketSummary[]`, `Ticket`, `DepNeighborhood`, `Project`, `Roadmap` re-exports in `src/lib/api/index.ts`).
8. Vitest tests using `vi.stubGlobal('fetch',...)`: success GET; 404 envelope -> `ApiError` with `code/status/message`; network failure -> `ApiError` `code=network_error`; query params URL-encoded for `listTickets`.

## Critical files

- `frontend/package.json`
- `frontend/openapi-codegen.config.ts`
- `frontend/scripts/postcodegen.mjs`
- `frontend/src/lib/api/types.ts`
- `frontend/src/lib/api/errors.ts`
- `frontend/src/lib/api/client.ts`
- `frontend/src/lib/api/client.test.ts`
- `frontend/src/lib/api/index.ts`

## Interface & data

Client exports typed by generated `types.ts` (do NOT hand-write). Consumes REST v1 endpoints per contract. Same-origin (relative URL only, refuse absolute). Error envelope shape `{ error: { code, message, details? } }` normalized to `ApiError`.

## Verification

`pnpm codegen` against a running backend regenerates `types.ts` with banner intact; `pnpm test` passes the four client cases; from a route call `getTicket('nonexistent-id')` -> `ApiError` `status=404 code='ticket_not_found'`; `pnpm check` errors if a client return type drifts.
