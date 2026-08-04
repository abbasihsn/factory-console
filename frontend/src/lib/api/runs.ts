/**
 * The `GET /api/v1/runs` wrapper — what the factory recorded per manifest ticket.
 *
 * Its own module (rather than another function in `client.ts`) for the reason
 * `spend.ts` is: the runs view is the only consumer and the endpoint carries a
 * vocabulary — artifacts, skip reasons, results vs receipts — that belongs
 * together. It goes through the shared {@link request}, so it inherits the
 * same-origin refusal, the timeout, and the {@link ApiError} envelope every other
 * wrapper has.
 */
import { request } from './client';
import type { RunListResponse, RunRecord } from './models';

/**
 * `GET /api/v1/runs` — one record per MANIFEST ticket, in manifest order.
 *
 * Envelope-unwrapped exactly like `listTickets`/`searchTickets` (and unlike
 * `getSpend`, which is a single object): this is the `{ items, total }` list
 * shape, the server documents `total` as the record count with no filtering and
 * no pagination, so `total === items.length` and the envelope carries nothing the
 * array does not. The immutable generated array is copied so the caller gets a
 * mutable `RunRecord[]`, as the other two list wrappers do.
 *
 * An empty array therefore means the MANIFEST is empty — never that the factory
 * has not run. A project with no `.factory/` still answers one record per ticket,
 * each naming `absent` per source; that distinction is the whole point of the
 * endpoint and callers must read it off the records' `reason` fields.
 */
export async function getRuns(): Promise<RunRecord[]> {
	const response = await request<RunListResponse>('runs');
	return [...response.items];
}
