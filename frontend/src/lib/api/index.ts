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
	getGraph,
	searchTickets,
	getRoadmap,
	getHealth,
	createTicket,
	updateTicket,
	deleteTicket,
	previewWrite,
	TOKEN_HEADER,
	type ListTicketsParams,
	type SearchParams,
	type Filters,
	type WriteRequest
} from './client';
export { ApiError } from './errors';
export type { ApiErrorInit } from './errors';
export type {
	Project,
	Ticket,
	TicketSummary,
	TicketListResponse,
	RunState,
	Health,
	TicketGraph,
	GraphNode,
	GraphEdge,
	SearchResponse,
	SearchHit,
	Roadmap,
	RoadmapMilestone,
	RoadmapItem,
	DepNeighborhood,
	TicketCreate,
	TicketUpdate,
	WriteResult,
	WritePreview,
	DiffPreview,
	FileDiff
} from './models';
