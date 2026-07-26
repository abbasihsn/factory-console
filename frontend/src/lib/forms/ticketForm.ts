/**
 * PURE ticket-form logic — no Svelte, no I/O.
 *
 * This module is the client-side, exhaustively-testable mirror of the server's
 * ticket-id constraint and required-field rules. It is DEFENSE IN DEPTH only:
 * the server (Pydantic `TicketId` + the create/update endpoints) is the real
 * gate. This mirror gives the user immediate feedback but is NEVER the sole
 * validator.
 */

/**
 * Allowed characters for a ticket id.
 *
 * MIRRORS `factory_console.domain.ticket.TICKET_ID_PATTERN`, whose value is
 * exactly `r"^[A-Za-z0-9_.-]+$"` — source of truth:
 * `server/factory_console/domain/ticket.py`. Keep in sync with that constant;
 * this copy exists only for early client-side feedback, never as the sole gate.
 */
export const TICKET_ID_PATTERN = /^[A-Za-z0-9_.-]+$/;

/**
 * Raw form values as held by the edit/create form.
 *
 * `dependsOn` and `files` are newline-delimited strings while being edited in a
 * textarea; they are also the two the API takes as arrays, so they convert with
 * {@link parseList} / {@link serializeList} to and from the `string[]` that
 * `TicketCreate` / `TicketUpdate` declare.
 *
 * `provides` is neither: the write DTOs type it as a scalar `string` (see
 * `TicketDraft.provides` in `$lib/api/types.ts`, from `domain/write.py`'s
 * `provides: str = ""`), so it is edited in a single-line input and its value is sent
 * through as-is. Do not {@link parseList} it — the server stores the string verbatim
 * and the read model hands it back as a SINGLE-ELEMENT `Ticket.provides` list, so
 * anything that looks like a multi-entry list here collapses on the next round-trip.
 */
export interface TicketFormValues {
	/** Ticket id — required in `create` mode, fixed by the route in `edit` mode. */
	id: string;
	/** Human-readable title — required in both modes. */
	title: string;
	/** Newline-delimited list of ticket ids this ticket depends on. */
	dependsOn: string;
	/** The capability tag this ticket provides — a single scalar value on the wire. */
	provides: string;
	/** Newline-delimited list of file paths this ticket touches. */
	files: string;
	/**
	 * The ticket's markdown body. Optional so existing literals/tests that predate
	 * the editor stay valid; it is NOT a validated/required field — the server
	 * accepts an empty `bodyMarkdown`, so {@link validateTicketForm} ignores it.
	 */
	body?: string;
}

/**
 * Per-field validation errors, keyed by {@link TicketFormValues} field name.
 * An empty object means the form is valid.
 */
export type TicketFormErrors = Partial<Record<keyof TicketFormValues, string>>;

/**
 * Parse a newline-delimited textarea value into a clean list: split on newlines,
 * trim each entry, and drop empties. Round-trips with {@link serializeList} for
 * any already-clean list (`parseList(serializeList(xs))` equals `xs`).
 */
export function parseList(raw: string): string[] {
	return raw
		.split('\n')
		.map((item) => item.trim())
		.filter((item) => item.length > 0);
}

/**
 * Serialize a list back into a newline-delimited textarea value. Round-trips with
 * {@link parseList} for any already-clean (trimmed, non-empty) list.
 */
export function serializeList(items: string[]): string {
	return items.join('\n');
}

/**
 * Validate a ticket form.
 *
 * Rules:
 * - `title` is required in BOTH modes (non-empty after trim).
 * - `id` is required ONLY in `create` mode. In `edit` mode the id is fixed by the
 *   route, so a blank id is not a "required" error there.
 * - Whenever an `id` IS provided (either mode), it must match
 *   {@link TICKET_ID_PATTERN}; a non-matching id (spaces, slashes, etc.) is an
 *   error.
 *
 * @returns a per-field error map; an empty map means valid.
 */
export function validateTicketForm(
	values: TicketFormValues,
	opts: { mode: 'create' | 'edit' }
): TicketFormErrors {
	const errors: TicketFormErrors = {};

	if (values.title.trim().length === 0) {
		errors.title = 'Title is required.';
	}

	const id = values.id.trim();
	if (id.length === 0) {
		// Blank id is only an error when creating; in edit mode it comes from the route.
		if (opts.mode === 'create') {
			errors.id = 'Ticket id is required.';
		}
	} else if (!TICKET_ID_PATTERN.test(id)) {
		errors.id = 'Ticket id may only contain letters, digits, and _ . - characters.';
	}

	return errors;
}
