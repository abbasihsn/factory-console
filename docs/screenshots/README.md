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
| `search.png` | Full-text search results for `idempotent` (`/search`). |
| `graph.png` | The dependency graph (`/graph`). |
| `roadmap.png` | The roadmap / milestone view (`/roadmap`). |
| `switcher.png` | The header project switcher over a two-project console. |
| `projects.png` | The `/projects` registry table, listing both tracked projects. |
| `live.png` | The live-update indicator pill in its connected `Live` state. |

## Regenerate

From the repo root:

```
pnpm --dir frontend screenshots
```

That runs the `frontend/tests/e2e/screenshots.spec.ts` e2e (which boots a real
`factory-console` on the `with_run_state` fixture and captures the PNGs above
into the gitignored `frontend/tests/e2e/__screenshots__/`), then
`frontend/scripts/copy-screenshots.mjs` copies them here. The two multi-project
shots (`switcher.png`, `projects.png`) come from a second, dedicated console in
the same run, tracking `with_run_state` plus the `minimal` fixture — the shared
console stays single-project throughout. Commit the updated PNGs so the README
images stay in sync with the UI.
