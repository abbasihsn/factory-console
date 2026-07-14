# [T03] Frontend skeleton (SvelteKit + adapter-static + Tailwind + Vitest + Playwright + openapi-typescript)

milestone: MVP · track: foundation · depends_on: T01 · provides: Buildable SvelteKit SPA scaffold outputting static assets to `frontend/build/`, ready for `scripts/package.sh` to copy into `server/factory_console/_static/`

## Context

Lays the frontend toolchain so the frontend track can drop routes and components into `src/` without arguing about build config. Wires Tailwind (JIT), Vitest, Playwright, openapi-typescript, ESLint + Prettier. Configures `adapter-static` (SPA mode, `fallback` `index.html`) so the built output is a plain folder the wheel can bake in. No routes or components — a stub `+page.svelte` so `pnpm build` succeeds.

## Staged approach

1. `frontend/package.json` declaring devDependencies: `@sveltejs/kit`, `@sveltejs/adapter-static`, `svelte`, `vite`, `typescript`, `tailwindcss`, `postcss`, `autoprefixer`, `vitest`, `@testing-library/svelte`, `jsdom`, `@playwright/test`, `openapi-typescript`, `eslint`, `prettier`, `prettier-plugin-svelte`, `eslint-plugin-svelte`. Scripts: `dev`, `build`, `preview`, `check`, `test`, `e2e`, `codegen`, `lint`, `format`, `format:check`.
2. `svelte.config.js`: `adapter-static` with `pages='build'` `assets='build'` `fallback='index.html'` `strict:false`.
3. `vite.config.ts`: `sveltekit()`; `server.proxy` for `/api -> http://127.0.0.1:8000` (dev only).
4. `tailwind.config.js`: content globs on `src/**/*`.
5. `postcss.config.cjs`: tailwindcss + autoprefixer.
6. `tsconfig.json` extends `.svelte-kit/tsconfig.json` with `strict:true`.
7. `src/app.html`, `src/app.css` (`@tailwind base/components/utilities`).
8. `src/routes/+layout.svelte` importing `app.css`.
9. Placeholder `src/routes/+page.svelte` with a stub `h1`.
10. `frontend/.gitignore` (node_modules, build, .svelte-kit).
11. `.eslintrc.cjs` + `.prettierrc`.

## Critical files

- `frontend/package.json`
- `frontend/svelte.config.js`
- `frontend/vite.config.ts`
- `frontend/tailwind.config.js`
- `frontend/postcss.config.cjs`
- `frontend/tsconfig.json`
- `frontend/.prettierrc`
- `frontend/.eslintrc.cjs`
- `frontend/.gitignore`
- `frontend/src/app.html`
- `frontend/src/app.css`
- `frontend/src/routes/+layout.svelte`
- `frontend/src/routes/+page.svelte`

## Interface & data

Consumes REST v1 base `/api` via Vite dev proxy (production is same-origin, no proxy). No API calls yet. Wheel-embed contract: SPA output must land under `frontend/build/` so `scripts/package.sh` can copy into `server/factory_console/_static/`.

## Verification

`pnpm install`; `pnpm build` produces `frontend/build/index.html` + assets; `pnpm test` runs (0 tests, exit 0); `pnpm lint` clean.
