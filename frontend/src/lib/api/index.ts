/**
 * Public entry point for the typed API client.
 *
 * Re-exports the endpoint wrappers, the {@link ApiError} class the client throws,
 * and the domain types. The internal `request` helper is intentionally not
 * re-exported. Note this `ApiError` is the CLASS (from `./errors`); the
 * `ApiError` TYPE in `./contracts` is the separate render shape and is not
 * surfaced here to avoid a name collision.
 */
export {
	getProject,
	listTickets,
	getTicket,
	getTicketDeps,
	getRoadmap,
	getHealth,
	type Health,
	type ListTicketsParams
} from './client';
export { ApiError } from './errors';
export type { ApiErrorInit } from './errors';
export type {
	Project,
	Ticket,
	TicketSummary,
	TicketListResponse,
	RunState,
	Roadmap,
	DepNeighborhood
} from './models';
