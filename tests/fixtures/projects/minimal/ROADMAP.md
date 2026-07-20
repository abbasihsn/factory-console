# TrailMark Roadmap

TrailMark turns scattered ranger condition sheets into a live, trustworthy map
of what's passable right now. We plan in rolling waves: the MVP is fully
elaborated into PR-sized tickets, while later versions stay epic-level until we
plan them just in time.

## MVP — make ranger reports usable

The MVP proves the spine end to end: get real data in, keep it clean, and prove
the store is queryable.

- [x] Canonical trail-report schema and store
- [ ] Ingest trail reports from the CSV drop folder (TM-001)
- [ ] Operator log line per imported file
- [ ] Smoke fixture with one district's exports

## v1 — put conditions in front of hikers (epics)

- **Public read API** — a cache-friendly status endpoint per trail (TM-015),
  plus a 7-day condition trend for sparklines.
- **Crowd-sourced reports** — let trusted hikers submit observations from the
  mobile app, moderated into the same canonical store.
- **Search and map** — browse trails by region and current condition.

## v2 — proactive and personal (epics)

- **Washout alerts** — opt-in push notifications when a followed trail turns
  impassable (TM-028), with quiet hours and dedupe.
- **Personal trail lists** — follow trails, set condition thresholds.
- **Historical trends** — season-over-season passability analytics.

## Principles

1. Ranger and crowd data are read-through; TrailMark never edits a source feed.
2. Every milestone must be independently useful and deployable.
3. Advisory only — TrailMark supplements, never replaces, official closures.
