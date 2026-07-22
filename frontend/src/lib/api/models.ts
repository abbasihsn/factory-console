/**
 * Friendly names for the API domain types.
 *
 * The types backing endpoints present in the generated `./types` are aliased
 * straight from it (`--immutable`, so every field is `readonly`). `Roadmap` and
 * `DepNeighborhood` back the `getRoadmap` / `getTicketDeps` wrappers: those routes
 * (`/api/v1/roadmap`, `/api/v1/tickets/{id}/deps`) now exist on the backend
 * (T23/T24), but the committed `types.ts` was generated against an earlier backend
 * that predated them, so their schemas are not in it yet. They are hand-written
 * here to mirror the server response shapes and MUST be swapped for the generated
 * types once `types.ts` is regenerated against a backend that serves them — see the
 * banner below.
 *
 * Keeping the aliases and the two temporary types here (rather than in
 * `client.ts`) gives `client.ts`, `index.ts`, and `contracts.ts` one shared
 * source without any circular import.
 */
import type { components } from './types';

export type Project = components['schemas']['Project'];
export type Ticket = components['schemas']['Ticket'];
export type TicketSummary = components['schemas']['TicketSummary'];
export type TicketListResponse = components['schemas']['TicketListResponse'];
export type RunState = components['schemas']['RunState'];

/* -------------------------------------------------------------------------- *
 * TEMPORARY hand-written types — DELETE when `types.ts` is regenerated.
 *
 * `/api/v1/roadmap` and `/api/v1/tickets/{id}/deps` exist on the backend but are
 * absent from the committed `types.ts` (generated against an earlier backend), so
 * `openapi-typescript` has not emitted their schemas yet. These mirror the server
 * response shapes and use `readonly` to match the generated `--immutable` style.
 * After `pnpm codegen` against a backend that serves these routes, swap `Roadmap`
 * for `components['schemas']['RoadmapPresent'] | components['schemas']['RoadmapAbsent']`
 * and `DepNeighborhood` for `components['schemas']['DepNeighborhood']`, then remove
 * this block.
 * -------------------------------------------------------------------------- */

/**
 * Presence probe from `GET /api/v1/roadmap`: whether the discovered project has a
 * roadmap and, when present, its resolved path (a `Path` serializes to a string).
 * Presence-only in the MVP — the rendered `bodyMarkdown`/`bodyHtml` land with a
 * later milestone — so this is a discriminated union on `present`, not a document.
 */
export type Roadmap =
	{ readonly present: true; readonly path: string } | { readonly present: false };

/** Dependency neighborhood of one ticket from `GET /api/v1/tickets/{id}/deps`. */
export interface DepNeighborhood {
	readonly ticket: TicketSummary;
	readonly directDeps: readonly TicketSummary[];
	readonly directDependents: readonly TicketSummary[];
	readonly unresolvedDeps: readonly string[];
}
