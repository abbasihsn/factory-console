# [T43] Widen GET /api/v1/roadmap to full body + structured milestones

milestone: v1 · track: backend · depends_on: T38, T24 · provides: GET /api/v1/roadmap returning the full Roadmap (bodyMarkdown + bodyHtml + structured milestones) or { present:false }

## Context

The v1 `/roadmap` view needs the full rendered `ROADMAP.md` plus structured milestones, not the MVP's presence-only probe. Per the ARCHITECTURE REST contract (`GET /api/v1/roadmap -> Roadmap | { present:false }`) this fully realizes the endpoint. T24 deliberately avoided calling `adapter.get_roadmap` (wasteful for presence-only); v1 flips that to serve the widened `Roadmap` the file-adapter now parses (T38). This is a single-file widen of the existing handler — no new route, so it does NOT touch the router registry and stays parallel to the other v1 backend tickets.

## Staged approach

1. In `server/factory_console/api/v1/roadmap.py`, change the handler to load the project (`app.state.project_root` → `adapter.load_project`) and call `adapter.get_roadmap(project)`: return the full `Roadmap` domain model when non-`None` (now carrying `bodyMarkdown`, `bodyHtml`, and the new `milestones[]`), else the existing `RoadmapAbsent`.
2. Update the return annotation to `Roadmap | RoadmapAbsent` (matching the architecture contract `Roadmap | { present:false }`) and import `Roadmap` from `domain`; remove the now-superseded `RoadmapPresent` model.
3. Refresh the module + handler docstrings to state the endpoint now serves the full rendered body + milestones (dropping the "presence-only in MVP" note). Let a `RoadmapUnreadable` (500) from the adapter read propagate to the registered `FactoryConsoleError` handler — do not catch it here.

## Critical files

- `server/factory_console/api/v1/roadmap.py` (widen in place — the only file this ticket touches)

## Interface & data

- Request: `GET /api/v1/roadmap` (no params). Response 200: `Roadmap { path, bodyMarkdown, bodyHtml, milestones: RoadmapMilestone[] }` when present, else `RoadmapAbsent { present: false }` (backend-owned, kept).
- `Roadmap` + `RoadmapMilestone` are the T38 file-adapter models (referenced, not redefined). Error: `RoadmapUnreadable` (500, code `roadmap_unreadable`, already defined in `file_adapter/real.py`) now reachable and rendered by the existing domain-error handler.
- Consumes: `FileAdapter.get_roadmap`, `Project`. No DB. NFR: no cache. Discriminator: `RoadmapAbsent` carries `present:false`; `Roadmap` has no such field (frontend discriminates on its presence).

## Verification

`pytest` integration test over `create_app(FakeFileAdapter)`: with a roadmap seeded, `GET /api/v1/roadmap` returns 200 with `bodyHtml` + non-empty `milestones` and NO `present` key; with no roadmap, returns `{present:false}`. Add a RealFileAdapter/`tmp_path` case (or fake that raises) asserting an unreadable roadmap yields the 500 `roadmap_unreadable` envelope. Confirm the widened `Roadmap` (with `milestones`) is in `/api/v1/openapi.json`.
