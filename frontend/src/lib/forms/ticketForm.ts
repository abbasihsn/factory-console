/**
 * PURE ticket-form logic — no Svelte, no I/O.
 *
 * This module is the client-side, exhaustively-testable mirror of the server's
 * ticket-id constraint and required-field rules. It is DEFENSE IN DEPTH only:
 * the server (Pydantic `TicketId` + the create/update endpoints) is the real
 * gate. This mirror gives the user immediate feedback but is NEVER the sole
 * validator.
 *
 * **A ticket is five structured fields now, not a markdown body.** App Factory v3
 * stores ticket content as JSON (`schemas/ticket.schema.json`) and renders Markdown
 * as a VIEW of it, so the form collects Context, Staged approach, Critical files,
 * Interface & data and Verification separately rather than one free-text area. The
 * schema sets `additionalProperties: false`, so there is nowhere to put prose that
 * belongs to no field — which is why this is a change to what a user writes, not
 * only to how it is stored.
 */

// Type-only, so this module stays free of any runtime dependency on `$lib/api`
// (which does fetch) and remains importable from a plain unit test.
import type { TicketCreate, TicketUpdate } from '$lib/api';

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
 * `dependsOn`, `criticalFiles` and `verificationCommands` are newline-delimited
 * strings while being edited in a textarea; all three are arrays on the wire, so they
 * convert with {@link parseList} / {@link serializeList}.
 *
 * `provides` is not: the write DTOs type it as a scalar `string` (`domain/write.py`'s
 * `provides: str = ""`), so it is edited in a single-line input and sent as-is. Do not
 * {@link parseList} it — the server stores the string verbatim and the read model hands
 * it back as a SINGLE-ELEMENT `Ticket.provides` list, so anything that looks like a
 * multi-entry list here collapses on the next round-trip.
 *
 * `verificationNotes` is the ONE optional content field, matching the schema. Everything
 * else is required, and the form says so before the server has to.
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
	/** Why this ticket exists, what it delivers, how it fits the sub-version. */
	context: string;
	/** Ordered build steps — the files to create or modify, in order. */
	approach: string;
	/**
	 * Newline-delimited list of every file this ticket creates or modifies.
	 *
	 * Not a formality: this is the one content field the factory acts on mechanically.
	 * It feeds the overlap filter that serializes two lanes which would otherwise edit
	 * the same path off bases lacking each other's changes, so a short list does not
	 * fail loudly — it silently weakens a concurrency guard. At least one entry.
	 */
	criticalFiles: string;
	/** Inputs/outputs, contracts, entities touched — or the literal `N/A`. */
	interfaceData: string;
	/**
	 * Newline-delimited shell commands that verify this slice, run from the repo root.
	 *
	 * At least one, per the schema: under INV-42 a verification that cannot run is not
	 * a pass, so a ticket declaring no command can never be verified, only assumed.
	 */
	verificationCommands: string;
	/** Optional context the commands need but cannot express (an env var, a service). */
	verificationNotes?: string;
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
 * The five content fields, in their wire shapes. Shared by create and edit.
 *
 * `verificationNotes` is omitted when blank rather than sent as `""`, matching the
 * server's own rendering rule: the schema makes `notes` optional, and a key
 * present-and-empty is a different document from a key absent — it would show as an
 * added line in the diff of every ticket that has no notes, and it claims the author
 * answered a question they did not.
 */
function contentFields(values: TicketFormValues) {
	const notes = values.verificationNotes?.trim() ?? '';
	return {
		context: values.context.trim(),
		approach: values.approach.trim(),
		criticalFiles: parseList(values.criticalFiles),
		interfaceData: values.interfaceData.trim(),
		verificationCommands: parseList(values.verificationCommands),
		...(notes.length === 0 ? {} : { verificationNotes: notes })
	};
}

/**
 * Build the PUT body for one set of form values.
 *
 * **THE OMIT-WHEN-NEVER-SET GUARD IS GONE, and its disappearance is the point.** It
 * existed to protect a value that lived in TWO places — the manifest entry and the
 * ticket `.md`'s YAML header — from an edit that meant to touch neither: sending the
 * form's empty value would overwrite the header's only correct copy, permanently, since
 * every later edit re-based off the wiped one. A v3 ticket has no header. Every field
 * lives in exactly one file, the content file is replaced wholesale, and the manifest
 * entry is still merged for the keys this form does not name. There is no second copy
 * left to destroy, so the guard now protects nothing and only hides what is being sent.
 *
 * `track` and `milestone` are still omitted, for a different and surviving reason: this
 * form does not collect them, so sending anything would be inventing a value. The server
 * refreshes a field only where the request supplied it.
 */
export function toTicketUpdate(values: TicketFormValues): TicketUpdate {
	return {
		title: values.title.trim(),
		dependsOn: parseList(values.dependsOn),
		provides: values.provides.trim(),
		...contentFields(values)
	} as TicketUpdate;
}

/**
 * Build the POST body for one set of form values, in CREATE mode.
 *
 * Now identical in shape to {@link toTicketUpdate} plus the `id`, which is what the
 * server's own DTOs look like since `TicketDraft` became `TicketEdit` plus an id. The
 * two used to diverge because edit carried the header-protection guard create had no
 * need for; with that gone, so is the divergence.
 */
export function toTicketCreate(values: TicketFormValues): TicketCreate {
	return {
		id: values.id.trim(),
		title: values.title.trim(),
		dependsOn: parseList(values.dependsOn),
		provides: values.provides.trim(),
		...contentFields(values)
	} as TicketCreate;
}

/** The required content fields and the message shown when each is blank. */
const REQUIRED_TEXT_FIELDS: ReadonlyArray<[keyof TicketFormValues, string]> = [
	['context', 'Context is required — why this ticket exists and what it delivers.'],
	['approach', 'Staged approach is required — the ordered build steps.'],
	['interfaceData', 'Interface & data is required — write N/A if there is none.']
];

/** The required LIST fields and the message shown when each parses to nothing. */
const REQUIRED_LIST_FIELDS: ReadonlyArray<[keyof TicketFormValues, string]> = [
	['criticalFiles', 'At least one critical file — the overlap filter reads this list.'],
	[
		'verificationCommands',
		'At least one verification command — a check that cannot run is not a pass.'
	]
];

/**
 * Validate a ticket form.
 *
 * Rules:
 * - `title` is required in BOTH modes (non-empty after trim).
 * - `id` is required ONLY in `create` mode. In `edit` mode the id is fixed by the
 *   route, so a blank id is not a "required" error there.
 * - Whenever an `id` IS provided (either mode), it must match
 *   {@link TICKET_ID_PATTERN}; a non-matching id (spaces, slashes, etc.) is an error.
 * - Every content field except `verificationNotes` is required, mirroring the schema.
 *   The two list fields must parse to at least one entry — a textarea holding only
 *   whitespace passes "did you type something?" and answers nothing, which is exactly
 *   the shape `minItems: 1` exists to reject.
 *
 * The messages say WHY rather than restating the rule, because these two fields are
 * the ones a user is most likely to leave thin, and "at least one entry" does not
 * explain what breaks when they do.
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

	for (const [field, message] of REQUIRED_TEXT_FIELDS) {
		if ((values[field] as string).trim().length === 0) {
			errors[field] = message;
		}
	}
	for (const [field, message] of REQUIRED_LIST_FIELDS) {
		if (parseList(values[field] as string).length === 0) {
			errors[field] = message;
		}
	}

	return errors;
}
