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
// `getSpend` lives in its own module rather than in `client.ts`; it is re-exported
// here so every caller still reaches the client through this one barrel.
export { getSpend } from './spend';
// `getRuns` likewise lives in its own module, re-exported here so the barrel stays
// the one way in.
export { getRuns } from './runs';
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
	FileDiff,
	SpendResponse,
	TicketSpend,
	ModelSpend,
	LevelSpend,
	RunListResponse,
	RunRecord,
	ArtifactRead,
	ArtifactSkipReason
} from './models';
