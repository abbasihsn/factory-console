# [T121] Registry API client (`lib/api/projects.ts`) + regenerated types

milestone: v3.0 · track: frontend · depends_on: T113, T103, T28, T64, T66 · provides: a typed, tested door to the console registry — `listProjects` / `addProject` / `removeProject` / `selectProject` — plus the ONE regeneration of `src/lib/api/types.ts` for the registry endpoints.

## Context

Every v3.0 frontend slice reads or mutates the console's project registry, and the SPA may only reach
it through REST v1. This ticket adds the client module and is the **single place `types.ts` is
regenerated** for the registry endpoints, so no sibling frontend ticket touches that generated file
— it is a DO-NOT-EDIT artifact of `pnpm codegen` + `scripts/postcodegen.mjs`, and two tickets
regenerating it in parallel is a guaranteed merge conflict.

It delivers no UI on its own; the console is unchanged and `factory-console PATH` still works exactly
as today.

**Authorization is part of this module's contract.** All three registry mutations are write-token
gated (T113), so the mutating wrappers take the session token and send it in `TOKEN_HEADER` exactly
as `sendWrite` does, and a `401 write_token_invalid` surfaces as an `ApiError` the callers
(T122–T124) route to the existing `WriteTokenPrompt`. The read wrappers send no token.

## Staged approach

1. Boot a server carrying T112/T113's endpoints (`scripts/dev.sh`, or any local run) and regenerate:
   `FC_OPENAPI_URL=http://127.0.0.1:<port>/api/v1/openapi.json pnpm --dir frontend codegen`. Never
   hand-edit `src/lib/api/types.ts`; `postcodegen.mjs` re-prepends the banner. Note in the PR body
   that this file's diff is machine-generated.
2. In `src/lib/api/models.ts`, add friendly aliases for the new schemas — each
   `components['schemas'][...]`, nothing hand-written: the registry entry, the list envelope, and the
   selection shape. Document in the alias comment that `RegisteredProject` is a console-DB row and is
   DISTINCT from the read-through `Project`, per ARCHITECTURE.md "Data-model additions (v3)".
   The per-row field is **`condition`** (T103's union), not `availability`.
3. CREATE `src/lib/api/projects.ts` following the `runs.ts` / `spend.ts` per-area convention: every
   call goes through the shared `request()` from `./client` so it inherits the same-origin refusal,
   the timeout and the `ApiError` envelope. The read wrapper unwraps `{ items, total }` with a copied
   array exactly like `getRuns`. Mutating wrappers take the session write token and send it in
   `TOKEN_HEADER` with `content-type: application/json`, mirroring `sendWrite`; ids are
   `encodeURIComponent`-escaped exactly like `getTicket`.
4. Re-export the wrappers and the new type aliases from the barrel `src/lib/api/index.ts` (the
   established one-way-in rule).
5. Note for consumers: `GET /api/v1/health`'s `projectRoot` is **nullable** as of T116 — check any
   existing client code or type assumption that treated it as a string.
6. CREATE `src/lib/api/projects.test.ts` (Vitest, mocked global fetch, mirroring `runs.test.ts`):
   envelope unwrap; the token header present on each mutation and absent on the read; id escaping; a
   4xx envelope becoming an `ApiError` carrying the server's `code`; a `401 write_token_invalid`
   surfacing with that code intact so callers can branch on it; a network failure becoming
   `network_error`.

## Critical files

- `frontend/src/lib/api/types.ts` (regenerate — DO NOT EDIT BY HAND; aggregation file)
- `frontend/src/lib/api/models.ts` (modify — aggregation file)
- `frontend/src/lib/api/projects.ts` (create)
- `frontend/src/lib/api/index.ts` (modify — aggregation file)
- `frontend/src/lib/api/projects.test.ts` (create)

## Interface & data

`listProjects(): Promise<RegisteredProjectOut[]>`;
`addProject(body: { path: string; name?: string }, token: string): Promise<RegisteredProjectOut>`;
`removeProject(id: string, token: string): Promise<void>`;
`selectProject(id: string, token: string): Promise<CurrentSelection>`.
Paths come from the OpenAPI document (T112/T113 own them), not redefined here.

Contracts by reference: ARCHITECTURE.md "Contracts → REST v1" (envelope, camelCase,
`{ error: { code, message, details? } }`); "Data-model additions (v3) → RegisteredProject"; the v2
write-token scheme (`WRITE_TOKEN_HEADER` / the `FactoryWriteToken` security scheme); T103's
`RegistryEntryCondition`.

DB ops: none client-side. NFR flags: auth = `X-Factory-Write-Token` on mutations only, with the
`write_token_invalid` 401 preserved for callers; no caching (every read is fresh, matching the
console's read-through discipline); no retries.

## Verification

`pnpm --dir frontend test` (the new spec plus the whole Vitest suite);
`pnpm --dir frontend check` (svelte-check clean against the regenerated types);
`pnpm --dir frontend lint`; and `make lint` at the repo root. Confirm `types.ts` still carries the
DO-NOT-EDIT banner and that `git diff` on it shows only codegen output.
