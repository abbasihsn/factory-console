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

/** Liveness-probe body from `GET /api/v1/health` (`ok`, `version`, `projectRoot`). */
export type Health = components['schemas']['HealthResponse'];

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

/**
 * Request body for `POST /api/v1/tickets`. The server publishes this schema as
 * `TicketDraft`; `TicketCreate` is the friendly name the client uses. Requires
 * `id`, `title`, and `bodyMarkdown`.
 */
export type TicketCreate = components['schemas']['TicketDraft'];

/**
 * Request body for `PUT /api/v1/tickets/{id}`. Published as `TicketEdit` —
 * identical to {@link TicketCreate} minus `id`, which comes from the path.
 */
export type TicketUpdate = components['schemas']['TicketEdit'];

/**
 * Uniform response body of EVERY write verb — create, update, and delete, on
 * both the applying and the dry-run path. An apply sets `applied: true` and
 * carries the re-read `ticket`; a dry-run sets `applied: false`, `ticket: null`,
 * and reports the paths/diff that WOULD be written.
 */
export type WriteResult = components['schemas']['WriteResult'];

/**
 * A dry-run preview.
 *
 * The server publishes NO separate preview schema: `?dryRun=true` returns the same
 * {@link WriteResult} envelope with `applied: false` and `ticket: null`. So this is
 * an alias, not a distinct shape — it exists to let a preview-rendering call site
 * name what it is holding without implying a second contract.
 */
export type WritePreview = WriteResult;

/** The per-file diffs one write produces, carried as a {@link WriteResult}'s `diff`. */
export type DiffPreview = components['schemas']['DiffPreview'];

/** A single file's planned or applied change as a unified diff, inside {@link DiffPreview}. */
export type FileDiff = components['schemas']['FileDiff'];
