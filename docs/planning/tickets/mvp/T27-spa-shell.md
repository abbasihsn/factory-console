# [T27] SPA shell + routing + Tailwind base + global error page

milestone: MVP · track: frontend · depends_on: T03, T21 · provides: SPA shell with top bar (`projectRoot` + Reload), Tailwind base, global `+error.svelte` boundary. First runnable frontend once backend serves `/api/v1/project`

## Context

Establishes the SvelteKit SPA shell every subsequent route hangs off. Delivers working `pnpm dev` + `pnpm build`, top bar showing resolved `projectRoot` from `GET /api/v1/project`, Tailwind base + design tokens, and a global `+error.svelte` rendering `ApiError`. Bundles the error page here because it's a ~30-line addition sharing dependencies.

## Staged approach

1. `svelte.config.js` already has `adapter-static + prerender=false` from T03 — confirm SPA mode.
2. `src/routes/+layout.ts` fetches `GET /api/v1/project` once on mount, returns `{ project }` (or error).
3. `src/routes/+layout.svelte` renders top bar: app name, `project.rootPath` (monospace, truncated), Reload button calling `invalidateAll()`.
4. `src/routes/+error.svelte` reads `$page.error`, renders normalized `ApiError` (code + message + hint) + Reload link.
5. Replace T03's placeholder `src/routes/+page.svelte` with a slightly better placeholder (list route lands in T30).
6. Vitest smoke: layout renders top bar with supplied `project` prop; error page renders supplied error object.
7. `src/app.css` has `@tailwind base/components/utilities` + a small `:root` design-tokens block (bg, surface, text, muted, accent, danger).

## Critical files

- `frontend/src/routes/+layout.ts`
- `frontend/src/routes/+layout.svelte`
- `frontend/src/routes/+layout.test.ts`
- `frontend/src/routes/+error.svelte`
- `frontend/src/routes/+error.test.ts`
- `frontend/src/routes/+page.svelte`
- `frontend/src/app.css`

## Interface & data

Consumes REST v1 `GET /api/v1/project` (`Project` entity). Uses generated types once T28 lands; for now temporarily imports a narrow hand-written `{ rootPath: string }` type and swaps to the generated `Project` when T28 lands (called out in the PR).

## Verification

`pnpm build` succeeds; against a running backend, visiting `/` shows top bar with root + Reload button; `pnpm test` passes layout + error smoke tests; force an error (point client at unreachable URL) -> `+error.svelte` renders.
