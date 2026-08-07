/**
 * The `/api/v1/projects` registry wrappers — the console's project switcher.
 *
 * Its own module (rather than more functions in `client.ts`) for the reason
 * `runs.ts` and `spend.ts` are: this endpoint family carries a vocabulary of its
 * own — registry rows, the reserved `session` row, `condition`, the current
 * selection — that belongs together, and the switcher is its only consumer.
 * Every call goes through the shared {@link request}, so all five inherit the
 * same-origin refusal, the request timeout and the `ApiError` envelope.
 *
 * **The two reads send no token; all three mutations do.** The server gates only
 * the mutations (`POST`, `DELETE`, `PUT /current`), so the wrappers below take
 * the session write token and send it in {@link TOKEN_HEADER} exactly as
 * `client.ts`'s `sendWrite` does. A rejected token comes back as the `401`
 * `write_token_invalid` envelope with its code intact, which is what lets a
 * caller route it to the write-token prompt instead of rendering it as a failure
 * of the registry.
 */
import { request, TOKEN_HEADER } from './client';
import { ApiError } from './errors';
import type { CurrentSelection, ProjectListResponse, RegisteredProjectOut } from './models';

/** `DELETE /projects/{id}`'s success status — a bodiless `204`. See {@link removeProject}. */
const NO_CONTENT = 204;

/**
 * The `RequestInit` for one registry mutation: the verb, the write token, and the
 * JSON body when there is one.
 *
 * Shaped like `client.ts`'s `sendWrite` — same header object, same
 * `JSON.stringify` — and factored out here so the three mutations cannot come to
 * disagree about how the token travels. A `content-type` only describes a body,
 * so the `DELETE` (which sends none) does not claim one.
 */
function writeInit(method: 'POST' | 'PUT' | 'DELETE', token: string, body?: unknown): RequestInit {
	const payload = body === undefined ? undefined : JSON.stringify(body);
	return {
		method,
		headers: {
			[TOKEN_HEADER]: token,
			...(payload === undefined ? {} : { 'content-type': 'application/json' })
		},
		body: payload
	};
}

/**
 * `GET /api/v1/projects` — every row the switcher offers, session row first.
 *
 * Envelope-unwrapped exactly like `getRuns`/`listTickets`: the server documents
 * `total` as `len(items)` with no filtering and no pagination, so the envelope
 * carries nothing the array does not, and the immutable generated array is copied
 * so the caller gets a mutable `RegisteredProjectOut[]`.
 *
 * No row is ever dropped: a project whose directory was deleted or cannot be read
 * still appears, with the `condition` that names its state. Callers must read
 * that field rather than infer availability from a row's presence. This is a
 * read, so it sends no write token.
 */
export async function listProjects(): Promise<RegisteredProjectOut[]> {
	const response = await request<ProjectListResponse>('projects');
	return [...response.items];
}

/**
 * `POST /api/v1/projects` — start tracking `body.path`, returning the row it became.
 *
 * `201` with the created row, which carries three facts only the server holds
 * (the minted id, `addedAt`, and the probed `condition`). NOT idempotent: adding
 * a directory that is already tracked is a `409 duplicate_project_path`, not a
 * silent no-op. Adding does not select — the returned row always reports
 * `selected: false`.
 *
 * `name` is optional; omitted, the server labels the row with the directory's
 * final component.
 */
export function addProject(
	body: { path: string; name?: string },
	token: string
): Promise<RegisteredProjectOut> {
	return request<RegisteredProjectOut>('projects', writeInit('POST', token, body));
}

/**
 * `DELETE /api/v1/projects/{id}` — stop tracking the row. Nothing on the
 * project's own disk changes.
 *
 * The id is `encodeURIComponent`-escaped exactly like `getTicket`'s, so it can
 * never break out of the same-origin path.
 *
 * **Why the `catch`.** This is the one route in the API that answers a bodiless
 * `204`, and the shared {@link request} parses the success body unconditionally —
 * deliberately, so a 2xx that is not the expected JSON cannot escape as a raw
 * `SyntaxError`. An empty `204` therefore arrives here as that helper's
 * `invalid_response` `ApiError` at status 204, which for THIS route is success,
 * not a fault. Absorbing exactly that one pair here — rather than teaching
 * `request` about empty bodies — keeps the shared helper's contract ("a 2xx with
 * an unreadable body is an error") intact for every other wrapper, which all do
 * expect a body. Every other `ApiError`, the `401`/`404`/`409` envelopes
 * included, is rethrown untouched.
 */
export async function removeProject(id: string, token: string): Promise<void> {
	try {
		await request<null>(`projects/${encodeURIComponent(id)}`, writeInit('DELETE', token));
	} catch (error) {
		if (
			error instanceof ApiError &&
			error.code === 'invalid_response' &&
			error.status === NO_CONTENT
		) {
			return;
		}
		throw error;
	}
}

/**
 * `PUT /api/v1/projects/current` — point the console at `id` and report what it
 * now serves.
 *
 * The id travels in the BODY (`{ projectId }`), because the resource being
 * replaced is "what is selected" and the id is its new value, not its address —
 * so there is no path segment to escape here.
 *
 * Answers the same {@link CurrentSelection} as `GET /projects/current`, resolved
 * AFTER the switch, so a caller can feed the response straight into the header it
 * would otherwise refetch. A degraded target is not refused: selecting a project
 * whose directory is gone succeeds and reports `selected_project_missing`, which
 * is precisely the state an operator selects into in order to remove the row.
 */
export function selectProject(id: string, token: string): Promise<CurrentSelection> {
	return request<CurrentSelection>('projects/current', writeInit('PUT', token, { projectId: id }));
}
