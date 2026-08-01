# Cadence Roadmap

Cadence is a team habit tracker built on one idea: every streak, heatmap, and
digest is a pure fold over an append-only log of check-ins. We plan in rolling
waves — the MVP is fully ticketed, later versions stay epic-level until planned.

## MVP — check in and see your streak

- [x] Habit schema and append-only event store (CAD-100)
- [x] Streak computation service (CAD-118)
- [ ] Daily check-in REST endpoints (CAD-125)
- [ ] Minimal board UI

The MVP is done when a member can mark a habit and watch a streak grow.

## v1 — momentum (epics)

- **Weekly digest** — a Monday-morning recap of wins and slips (CAD-131).
- **Heatmap** — a year-at-a-glance contribution grid per habit (CAD-140).
- **Reminders** — nudge before a streak breaks.

## v2 — together (epics)

- **Shared rituals** — team-scoped habits with owner/member roles (CAD-152).
- **Team analytics** — participation and consistency across a team.
- **Integrations** — check in from Slack.

## Run-state note

This fixture ships a `.factory/run-state/` directory so the console can resolve
each ticket's lane. It deliberately spans every state: three `todo`, one
`in-flight`, one `ready`, and one `merged` — every ticket carries a marker, so
none exercises the directory's "present dir, no marker" default (`absent`); that
default is covered directly in the unit suite, not via this fixture.
