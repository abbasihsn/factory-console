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
- Types come from `src/lib/api/types.ts` (generated, `DO NOT EDIT`). Regenerate with `pnpm codegen` against a running backend.
