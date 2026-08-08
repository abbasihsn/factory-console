# Roadmap — factory-v3 fixture

Two sub-versions, no checkboxes. Status is rendered from `.factory/run-state.json`,
never stored here — a hand-ticked box is a copy that goes stale unnoticed, which is
what `factory-ticket lint` now fails a planning doc for.

## v1.0 — the foundation

A usable, deployable slice: the package builds, the entry point runs, one capability works.

- **T01** — The base ticket everything depends on
- **T02** — Depends on T01, in the same sub-version

## v1.1 — the view

Renders what v1.0 built. Cut off the new `main` once v1.0's PR is merged.

- **T03** — Depends on both, in a LATER sub-version
