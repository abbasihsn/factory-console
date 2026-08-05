import type { PageLoad } from './$types';
import { getRuns, listTickets, type RunRecord, type RunState } from '$lib/api';
// `throwBoundaryError` is imported DIRECTLY from its own module (not via the
// `$lib/api` barrel) so the load test can mock the barrel down to just
// `{ listTickets, getRuns }` and still reach the real boundary policy — mirroring
// the graph, roadmap and spend loaders.
import { throwBoundaryError } from '$lib/api/loadError';

/**
 * One table row: a manifest ticket, its run-state, and the factory artifacts for it.
 *
 * `record` is nullable only for the pathological case where the two endpoints
 * disagree about the manifest (a ticket listed by `/tickets` with no record in
 * `/runs`). Both are composed from the same manifest, so it should not happen —
 * but rendering such a row as if its artifacts were `absent` would state, as a
 * fact about the factory, something that is actually a fact about the console's
 * two reads disagreeing. It is carried as `null` and named in the view instead.
 */
export interface RunRow {
	readonly ticketId: string;
	readonly title: string;
	readonly runState: RunState;
	readonly record: RunRecord | null;
}

// SPA-only load: `ssr`/`prerender` are already false globally in
// `routes/+layout.ts`, so no need to re-declare them here.
//
// TWO endpoints, in parallel. `/runs` carries the artifacts and NOT the run-state
// (it is per-ticket state derived from `.factory/run-state`, which only
// `GET /tickets` resolves), so the badge column needs `listTickets` beside it.
// A project with no `.factory/` at all is NOT an error here: it answers 200 with a
// full list of records naming `absent` per source, and the page renders that as an
// explicit state. Only a genuine transport/server failure reaches the boundary.
export const load: PageLoad = async () => {
	try {
		const [tickets, runs] = await Promise.all([listTickets(), getRuns()]);

		// The MANIFEST is the list — the same rule the server applies — so the ticket
		// list drives both the rows and their order, and a run record is evidence
		// attached to a ticket rather than a row of its own. A record whose ticket is
		// not in the manifest is therefore dropped; it cannot be rendered as a ticket.
		const byTicketId = new Map(runs.map((record) => [record.ticketId, record]));
		const rows: RunRow[] = tickets.map((ticket) => ({
			ticketId: ticket.id,
			title: ticket.title,
			runState: ticket.runState,
			record: byTicketId.get(ticket.id) ?? null
		}));

		return { rows };
	} catch (err) {
		throwBoundaryError(err);
	}
};
