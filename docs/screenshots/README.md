# Screenshots

These PNGs are **generated**, not hand-drawn — they are captured from the real UI
by the Playwright screenshots pipeline and embedded in the repo-root
[`README.md`](../../README.md). Do not hand-edit or replace them manually; they
will be overwritten on the next regeneration.

| File | View |
|---|---|
| `list.png` | The ticket list (`/`). |
| `detail.png` | The `CAD-125` ticket detail (`/tickets/CAD-125`). |
| `deps.png` | The `CAD-125` dependency neighborhood (`/tickets/CAD-125/deps`). |

## Regenerate

From the repo root:

```
pnpm --dir frontend screenshots
```

That runs the `frontend/tests/e2e/screenshots.spec.ts` e2e (which boots a real
`factory-console` on the `with_run_state` fixture and captures the three PNGs
into the gitignored `frontend/tests/e2e/__screenshots__/`), then
`frontend/scripts/copy-screenshots.mjs` copies them here. Commit the updated PNGs
so the README images stay in sync with the UI.
