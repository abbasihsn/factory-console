# [T38] Structured roadmap parse (milestones + checkbox state) in get_roadmap

milestone: v1 · track: file-adapter · depends_on: T07, T14, T17 · provides: Roadmap model widened with milestones[] (RoadmapMilestone/RoadmapItem) + pure parse_milestones; RealFileAdapter.get_roadmap returns full body + structured milestones (NO new Protocol method)

## Context

The MVP roadmap endpoint is presence-only and `RealFileAdapter.get_roadmap` returns just body markdown/HTML (ARCHITECTURE "## Data model": Roadmap "MVP detects presence; v1 renders full body"). v1's `/roadmap` view needs the full body PLUS a structured milestone breakdown (milestone name, its items, each item's checkbox state and optional ticket id) so the SPA can render a navigable milestone list linking to tickets. This EXTENDS the existing `Roadmap` model + `get_roadmap` rather than adding a new port method — the backend v1 `/roadmap` (T43) simply calls the already-present `adapter.get_roadmap`.

## Staged approach

1. In `server/factory_console/domain/deps.py` (where `Roadmap` lives) add two frozen models: `RoadmapItem { text: str, ticketId: str | None = None, done: bool | None = None }` and `RoadmapMilestone { name: str, items: list[RoadmapItem] }`, and add `milestones: list[RoadmapMilestone] = Field(default_factory=list)` to `Roadmap` (the default keeps every existing construction valid). Consumers reach `RoadmapMilestone`/`RoadmapItem` transitively via `Roadmap` and via OpenAPI, so do NOT touch `domain/__init__.py`.
2. Add `server/factory_console/file_adapter/roadmap_parse.py`: pure `parse_milestones(body_markdown: str) -> list[RoadmapMilestone]`. Walk lines: a `## ` heading opens a milestone (name = heading text); list-item lines (`- `/`* `) under the current milestone become `RoadmapItem`s — detect `[x]`/`[X]` → `done=True`, `[ ]` → `done=False`, no checkbox → `done=None`; extract `ticketId` as the first token matching `TICKET_ID_PATTERN` found either parenthesized `(CAD-100)` OR as a no-space bold `**T01**` (so a spaced bold label like `**Weekly digest**` is NOT mistaken for an id); `text` = the cleaned item label. Tolerant: never raises; a body with no headings yields `[]`.
3. `real.py`: in `get_roadmap`, after building body/bodyHtml, pass `milestones=parse_milestones(body)` into the `Roadmap(...)` construction (keep the existing `RoadmapUnreadable` read guard).
4. No `fake.py` change — `FakeFileAdapter.get_roadmap` already returns the seeded `Roadmap`, which tests now seed with milestones.

## Critical files

- `server/factory_console/domain/deps.py` (widen Roadmap; add RoadmapMilestone/RoadmapItem)
- `server/factory_console/file_adapter/roadmap_parse.py` (new — pure parse_milestones)
- `server/factory_console/file_adapter/real.py` (populate milestones in get_roadmap)

## Interface & data

- Pure `parse_milestones(body_markdown: str) -> list[RoadmapMilestone]`; `RealFileAdapter.get_roadmap(project) -> Roadmap | None` now populates `milestones`.
- Touched BY REFERENCE (do not redefine): the existing `Roadmap` domain model (extended, not replaced) and the `get_roadmap` FileAdapter method (behavior extended, signature unchanged, so the Protocol is untouched); `TICKET_ID_PATTERN` reused for id extraction.
- New nested models `RoadmapMilestone { name, items }` and `RoadmapItem { text, ticketId?, done? }` (frozen, `extra='forbid'`, camelCase).
- DB ops: N/A. NFR: no cache / re-read per request; read-only; parser is total (never raises on malformed markdown, mirroring the front-matter parser's tolerance).

## Verification

`pytest` unit tests for `parse_milestones` on inline strings AND both real fixtures (`tests/fixtures/projects/{with_run_state,minimal}/ROADMAP.md`): headings become milestones; `[x]`/`[ ]` map to `done` True/False; parenthesized `(CAD-131)` and bold `**T01**` ids extracted; spaced bold labels get `ticketId=None`; non-list prose ignored; empty/heading-less body → `[]`. Adapter test: `RealFileAdapter.get_roadmap` on `with_run_state` returns body + the expected milestone/item structure; `get_roadmap` on a project without `ROADMAP.md` still returns `None`. Update any MVP `get_roadmap` test that asserted an exact `Roadmap` now that `milestones` is populated. `file_adapter/` coverage >90%.
