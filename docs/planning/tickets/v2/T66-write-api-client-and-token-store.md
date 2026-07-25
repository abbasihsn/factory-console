# [T66] Write API client wrappers + regenerated types + write-token session store & prompt

milestone: v2 · track: frontend · depends_on: T28, T46, T64, T65 · provides: regenerated types.ts + createTicket/updateTicket/deleteTicket/previewWrite wrappers (write-token header) + writeToken sessionStorage store + WriteTokenPrompt.

## Context

v2 turns the read-only SPA into an editor; every mutation needs a typed transport and the per-session loopback write token. This foundation ticket regenerates the generated types from the new OpenAPI (adding the write endpoints' request/response schemas and the write-token security scheme) and adds the mutating client wrappers, a sessionStorage-backed token store, and the token-entry prompt. It is the base every other v2 frontend ticket builds on, and the ONLY ticket that touches the api-package files (no parallel-merge hazard).

## Staged approach

1. With the v2 backend running, run `pnpm codegen` to REGENERATE `src/lib/api/types.ts` from `/api/v1/openapi.json` (never hand-edit; the postcodegen banner re-applies).
2. In `models.ts`, add friendly aliases for the new generated schemas by reference: `TicketCreate`, `TicketUpdate` (request bodies), `WriteResult`/`WritePreview` (responses) — aliases only, do not redefine shapes.
3. In `client.ts`, add a `TOKEN_HEADER` const whose name MUST match the backend OpenAPI security scheme (`X-Factory-Write-Token`), and add wrappers `createTicket(body, token)` → POST `tickets`, `updateTicket(id, body, token)` → PUT `tickets/{id}`, `deleteTicket(id, token)` → DELETE `tickets/{id}`, `previewWrite(verb, id?, body?, token)` → same verb with `?dryRun=true`; each sets `method`, JSON `content-type`, `JSON.stringify(body)`, and the token header on the existing `request()` init.
4. DELETE returns 204/empty — add a small `requestVoid` (or branch `request` on empty/204 body) so an empty success body does not throw `invalid_response`.
5. Add `src/lib/stores/writeToken.ts`: a `writable<string|null>` hydrated from and mirrored to `sessionStorage` (guarded for SSR/no-window), with `setToken`/`clearToken`.
6. Add `src/lib/components/WriteTokenPrompt.svelte`: a small presentational form to paste the token printed to stderr, calling `setToken` (no `$app/*` imports so it unit-tests under jsdom).
7. Re-export the new wrappers, aliases, store, and prompt via `index.ts`.

## Critical files

- `frontend/src/lib/api/types.ts` (regenerated)
- `frontend/src/lib/api/client.ts`
- `frontend/src/lib/api/models.ts`
- `frontend/src/lib/api/index.ts`
- `frontend/src/lib/stores/writeToken.ts` (new)
- `frontend/src/lib/components/WriteTokenPrompt.svelte` (new)

## Interface & data

`createTicket(body: TicketCreate, token: string): Promise<WriteResult>` (POST /api/v1/tickets), `updateTicket(id, body: TicketUpdate, token): Promise<WriteResult>` (PUT), `deleteTicket(id, token): Promise<void>` (DELETE), `previewWrite(...): Promise<WritePreview>` (same verb + `?dryRun=true`). `writeToken` store: `writable<string|null>` + `setToken(t)`/`clearToken()`. Contracts by reference (single source = regenerated types.ts): `TicketCreate`/`TicketUpdate`/`WriteResult`/`WritePreview` schemas and the write-token security scheme; existing `{ error: { code, message, details? } }` envelope via `ApiError` (no new error handling). No DB. NFR: AUTH — write-token header required on all mutating calls; same-origin (absolute URLs still refused); errors normalized to `ApiError`.

## Verification

`pnpm codegen` regenerates types.ts cleanly (banner present; DELETE/PUT/POST paths appear). `pnpm check` (svelte-check, TS-strict) passes with the new wrappers/aliases. Co-located Vitest specs (`client.test.ts` additions, `writeToken.test.ts`): token header set on write calls, absolute-path refusal still holds, 204 delete resolves void, and the store round-trips through a mocked sessionStorage. `pnpm test` + `pnpm lint` green.
