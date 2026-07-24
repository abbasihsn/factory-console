/**
 * Friendly names for the API domain types.
 *
 * Every type is aliased straight from the generated `./types` (`--immutable`, so
 * every field is `readonly`). Keeping the aliases here (rather than in `client.ts`)
 * gives `client.ts`, `index.ts`, and `contracts.ts` one shared source without any
 * circular import.
 */
import type { components } from './types';

export type Project = components['schemas']['Project'];
export type Ticket = components['schemas']['Ticket'];
export type TicketSummary = components['schemas']['TicketSummary'];
export type TicketListResponse = components['schemas']['TicketListResponse'];
export type RunState = components['schemas']['RunState'];

export type TicketGraph = components['schemas']['TicketGraph'];
export type GraphNode = components['schemas']['GraphNode'];
export type GraphEdge = components['schemas']['GraphEdge'];

export type SearchResponse = components['schemas']['SearchResponse'];
export type SearchHit = components['schemas']['SearchHit'];

export type RoadmapMilestone = components['schemas']['RoadmapMilestone'];
export type RoadmapItem = components['schemas']['RoadmapItem'];

/**
 * `GET /api/v1/roadmap` returns the project's roadmap document, or an absence
 * marker when the project has no `ROADMAP.md`. This is an `anyOf` union: the
 * present branch (`Roadmap`) carries `path`/`bodyMarkdown`/`bodyHtml`/`milestones`
 * and NO `present` field, while the absent branch (`RoadmapAbsent`) sets
 * `present: false` — so consumers discriminate on the `present` key's presence.
 */
export type Roadmap = components['schemas']['Roadmap'] | components['schemas']['RoadmapAbsent'];

/** Dependency neighborhood of one ticket from `GET /api/v1/tickets/{id}/deps`. */
export type DepNeighborhood = components['schemas']['DepNeighborhood'];
