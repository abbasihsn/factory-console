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
- `/tickets/new` — create-ticket route with dry-run preview, review, and apply (T71)
- `/tickets/[id]` — ticket detail (T31)
- `/tickets/[id]/deps` — dep neighborhood (T32)
- `/projects` — registry management: register a project by path, and every tracked row, its probed condition, and per-row Select/Remove (T123, T124)
- `+error.svelte` — global error boundary rendering the normalized `ApiError` (T27)

## Rules

- The SPA is same-origin with the API — all fetches use relative URLs (`/api/v1/...`).
- **Never render markdown client-side.** Server ships sanitized `bodyHtml`; the ONE component that uses `{@html}` is `MarkdownBody.svelte` (T29).
- Writes (`createTicket`/`updateTicket`/`deleteTicket`, and `previewWrite` for the `?dryRun=true` preview) go through the same `request()` as reads and MUST carry this session's write token in the `X-Factory-Write-Token` header. The token lives in `src/lib/stores/writeToken.ts` (`sessionStorage`-backed, per tab) and is entered via `WriteTokenPrompt.svelte`; the server prints it to its own stderr at startup. All four resolve to the same `WriteResult` envelope — a dry-run is that envelope with `applied: false`. `selectProject` (the header project switcher, `PUT /projects/current`) and `addProject` (the `/projects` registration form, `POST /projects`) are a fifth and sixth token-carrying write, but resolve to `CurrentSelection` and `RegisteredProjectOut` respectively, not `WriteResult` — neither is creating or changing a ticket.
- **A rejected token is not a terminal error.** A `401 write_token_invalid` means the held token is known bad: drop it with `clearToken()`, re-raise the prompt saying it was rejected (not merely missing), and resume the parked write once a fresh one is entered. Four flows watch the _store_ rather than relying solely on one prompt's callback, because more than one prompt can be on screen at once (the switcher hosts its own `WriteTokenPrompt` in the header, alongside whichever the current route raises) and any of them may collect the token — but they react to it arriving differently: the edit flow, the project switch, and the registration form all resume their parked write, while the delete flow only drops its pending latch (resuming would pop a destructive confirmation nobody asked for).
- The detail route's write affordances are `EditGate.svelte` (the read-only banner) and `EditTicketModal.svelte` (the form → dry-run → review → apply sequence). Editability is the single predicate `src/lib/forms/editability.ts`, a client-side MIRROR of the server write-gate — the server enforces the real one. A write in flight is undismissable: `ConfirmDialog` and `DiffPreviewModal` refuse Cancel, Escape and the backdrop while `busy`, since dismissing cannot recall the request.
- Types come from `src/lib/api/types.ts` (generated, `DO NOT EDIT`). Regenerate with `pnpm codegen` against a running backend: it runs `openapi-typescript` (`--immutable`, so every field is `readonly`), then `scripts/postcodegen.mjs` to prepend the DO-NOT-EDIT banner. Override the source with `FC_OPENAPI_URL` (e.g. a saved `openapi.json`); it defaults to the dev backend at `http://127.0.0.1:8000/api/v1/openapi.json`.
