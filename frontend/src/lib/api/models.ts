/**
 * Friendly names for the API domain types.
 *
 * The four types backing endpoints that exist on the base backend are aliased
 * straight from the generated `./types` (`--immutable`, so every field is
 * `readonly`). `Roadmap` and `DepNeighborhood` back the `getRoadmap` /
 * `getTicketDeps` wrappers, whose endpoints (`/api/v1/roadmap`,
 * `/api/v1/tickets/{id}/deps`) are NOT in this base backend yet (they land with
 * tickets T23/T24), so their schemas are not in the generated OpenAPI document.
 * They are hand-written here to mirror the server domain models
 * (`server/factory_console/domain/deps.py`, frozen pydantic) and MUST be swapped
 * for the generated types once those endpoints ship — see the banner below.
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
 * TEMPORARY hand-written types — DELETE when the endpoints land.
 *
 * `/api/v1/roadmap` and `/api/v1/tickets/{id}/deps` are not exposed by this base
 * backend, so `openapi-typescript` cannot emit `Roadmap` / `DepNeighborhood`.
 * These mirror the frozen pydantic domain models and use `readonly` to match the
 * generated `--immutable` style. Swap each for
 * `components['schemas']['Roadmap' | 'DepNeighborhood']` after `pnpm codegen`
 * against a backend that serves those routes, then remove this block.
 * -------------------------------------------------------------------------- */

/** Rendered roadmap document from `GET /api/v1/roadmap` (Path serializes to a string). */
export interface Roadmap {
	readonly path: string;
	readonly bodyMarkdown: string;
	readonly bodyHtml: string;
}

/** Dependency neighborhood of one ticket from `GET /api/v1/tickets/{id}/deps`. */
export interface DepNeighborhood {
	readonly ticket: TicketSummary;
	readonly directDeps: readonly TicketSummary[];
	readonly directDependents: readonly TicketSummary[];
	readonly unresolvedDeps: readonly string[];
}
