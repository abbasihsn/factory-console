# [T54] v1 docs + README screenshots refresh (graph, roadmap, search, live-update)

milestone: v1 · track: frontend · depends_on: T47, T48, T49, T50, T34, T19 · provides: docs/usage.md updated for the new nav/search/graph/roadmap/live-update features + the screenshots pipeline extended to capture the three new views for the README gallery

## Context

Documentation is in-scope per ARCHITECTURE.md, and the MVP shipped docs (T19) + a Playwright screenshots→README pipeline (T34). v1 adds three new user-facing views (`/graph`, `/roadmap`, `/search`) and a live-update indicator, but nothing else in the v1 increment refreshes user docs or regenerates the README screenshots for them — so without this ticket `docs/usage.md` and the README gallery would show only the MVP list/detail/deps views after v1 lands. This is the milestone's documentation tail: polish, not a slice blocker, hence last.

## Staged approach

1. Extend the screenshots capture spec (the T34 pipeline, e.g. `frontend/tests/e2e/screenshots.spec.ts`) to navigate to and capture `/graph`, `/roadmap`, and `/search?q=<term>` (and, if practical, the live-update indicator state) against the `with_run_state` fixture, writing into the same screenshots output the README gallery consumes.
2. Update `docs/usage.md` to describe the new navigation (Graph/Roadmap links + global search box), the dependency-graph view, the roadmap/milestone view, and the live-update behavior (auto-refresh + indicator, graceful fallback).
3. Refresh the README quickstart/gallery section (or its generated screenshot references) so the new views appear alongside the MVP ones.
4. Keep everything consistent with the existing screenshot naming + README-embed conventions from T34 (do not restructure the pipeline — only add captures).

## Critical files

- `frontend/tests/e2e/screenshots.spec.ts` (extend — capture the new views; exact path per the T34 pipeline)
- `docs/usage.md` (describe the new features)
- `README.md` (gallery/quickstart references to the new views)

## Interface & data

- No code contract or API change — this is docs + screenshot capture only. Exercises `/graph`, `/roadmap`, `/search` through the UI (same click-navigation constraint as the other e2e specs — SPA static mount serves `/` only). Reuses the T34 screenshots harness + the `with_run_state` fixture.
- DB ops: N/A. NFR: deterministic capture (stable fixture, bounded waits for render, no fixed sleeps); no fixture mutation (read-only views; the live-update shot, if included, uses the same disposable-copy approach as T53 or is omitted to stay deterministic).

## Verification

Run the screenshots pipeline (per T34) and confirm new images for `/graph`, `/roadmap`, `/search` are produced and embedded; `docs/usage.md` renders and describes the v1 features; the README gallery shows the new views. `pnpm lint`/markdown checks (if any) pass. No application behavior changes — existing tests stay green.
