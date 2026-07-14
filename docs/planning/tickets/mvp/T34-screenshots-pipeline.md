# [T34] README screenshots pipeline (Playwright captures + copy script + docs/screenshots + README refresh)

milestone: MVP · track: frontend · depends_on: T33, T19 · provides: `screenshots.spec.ts` capturing list/detail/deps PNGs; `copy-screenshots.mjs` relocating them under `docs/screenshots/`; `README.md` updated to embed screenshots

## Context

Turns the Playwright harness into the source of README screenshots so docs stay in sync with actual UI. Absorbs the folded-in docs responsibility called out in the frontend context file. Kept separate from T33 because a broken screenshot pipeline should never mask a real e2e regression.

## Staged approach

1. `frontend/tests/e2e/screenshots.spec.ts`: navigate `/`, `/tickets/<known-id>`, `/tickets/<known-id>/deps` against the fixture and capture `list.png`, `detail.png`, `deps.png` into `frontend/tests/e2e/__screenshots__/`.
2. `frontend/scripts/copy-screenshots.mjs`: node script that reads `__screenshots__/` and writes each PNG to `docs/screenshots/`, creating the target dir if absent; wire as `postscreenshots` npm script or a `pnpm screenshots` alias.
3. Create `docs/screenshots/` (initially with a `.gitkeep` + a small README describing that the PNGs are generated).
4. Update `README.md` — rewrite the "Usage" section to embed `![list](docs/screenshots/list.png)`, `![detail](...)`, `![deps](...)`; note how to regenerate (`pnpm --dir frontend e2e --grep screenshots && node frontend/scripts/copy-screenshots.mjs`).

## Critical files

- `frontend/tests/e2e/screenshots.spec.ts`
- `frontend/scripts/copy-screenshots.mjs`
- `docs/screenshots/`
- `README.md`

## Interface & data

Consumes T33's Playwright harness + the `with_run_state` fixture UI. Produces static assets under `docs/screenshots/`. NFR: screenshots regeneration is a one-command loop; the script is idempotent (safe to re-run).

## Verification

`pnpm --dir frontend e2e --grep screenshots` produces three PNGs; `node frontend/scripts/copy-screenshots.mjs` copies them to `docs/screenshots/`; README links resolve and render on GitHub.
