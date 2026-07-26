# frontend/

The SvelteKit SPA served by the Python server. Built via `adapter-static` (SPA mode, `prerender=false`, `fallback=index.html`) and copied into `../server/factory_console/_static/` at package time.

## Planned stack

- SvelteKit + `@sveltejs/adapter-static`
- TypeScript (strict)
- Tailwind CSS (JIT)
- Vitest — unit tests
- Playwright — e2e (boots the packaged `factory-console` on the `with_run_state` fixture and drives a real browser)
- `openapi-typescript` — generates `src/lib/api/types.ts` from the server's `/api/v1/openapi.json`

## Routes (MVP)

- `/` — ticket list with server-side filter + search (T30)
- `/tickets/[id]` — ticket detail (T31)
- `/tickets/[id]/deps` — dep neighborhood (T32)
- `+error.svelte` — global error boundary rendering the normalized `ApiError` (T27)

## Rules

- The SPA is same-origin with the API — all fetches use relative URLs (`/api/v1/...`).
- **Never render markdown client-side.** Server ships sanitized `bodyHtml`; the ONE component that uses `{@html}` is `MarkdownBody.svelte` (T29).
- Writes (`createTicket`/`updateTicket`/`deleteTicket`, and `previewWrite` for the `?dryRun=true` preview) go through the same `request()` as reads and MUST carry this session's write token in the `X-Factory-Write-Token` header. The token lives in `src/lib/stores/writeToken.ts` (`sessionStorage`-backed, per tab) and is entered via `WriteTokenPrompt.svelte`; the server prints it to its own stderr at startup. All four resolve to the same `WriteResult` envelope — a dry-run is that envelope with `applied: false`.
- Types come from `src/lib/api/types.ts` (generated, `DO NOT EDIT`). Regenerate with `pnpm codegen` against a running backend: it runs `openapi-typescript` (`--immutable`, so every field is `readonly`), then `scripts/postcodegen.mjs` to prepend the DO-NOT-EDIT banner. Override the source with `FC_OPENAPI_URL` (e.g. a saved `openapi.json`); it defaults to the dev backend at `http://127.0.0.1:8000/api/v1/openapi.json`.
