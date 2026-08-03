/**
 * The `GET /api/v1/spend` wrapper — what the factory cost, three ways.
 *
 * Its own module (rather than another function in `client.ts`) because the spend
 * view is the only consumer and the endpoint carries a vocabulary — attribution,
 * source, skipped lines — that belongs together. It goes through the shared
 * {@link request}, so it inherits the same-origin refusal, the timeout, and the
 * {@link ApiError} envelope every other wrapper has.
 */
import { request } from './client';
import type { SpendResponse } from './models';

/**
 * `GET /api/v1/spend` — totals plus the per-ticket, per-model and per-level cuts.
 *
 * Not envelope-unwrapped: unlike `tickets`/`search` this is a single object, not
 * an `{ items, total }` list, so the body is returned as the server sent it —
 * including `source`, which is the ONLY way to tell "no ledger" from "$0.00".
 */
export function getSpend(): Promise<SpendResponse> {
	return request<SpendResponse>('spend');
}
