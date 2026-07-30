/**
 * PURE ticket-form logic — no Svelte, no I/O.
 *
 * This module is the client-side, exhaustively-testable mirror of the server's
 * ticket-id constraint and required-field rules. It is DEFENSE IN DEPTH only:
 * the server (Pydantic `TicketId` + the create/update endpoints) is the real
 * gate. This mirror gives the user immediate feedback but is NEVER the sole
 * validator.
 */

// Type-only, so this module stays free of any runtime dependency on `$lib/api`
// (which does fetch) and remains importable from a plain unit test.
import type { Ticket, TicketCreate, TicketUpdate } from '$lib/api';

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
 * Build the PUT body for one set of form values, for `ticket` as it was loaded.
 *
 * A mirrored field is sent only when there is something to say about it, and OMITTED
 * otherwise — never sent as `null` or empty just to fill the shape. Omitting is a
 * data-safety rule, not tidiness: every mirrored field exists twice, in the manifest
 * entry AND in the `.md` YAML header, but `Ticket` reads it from the manifest entry
 * alone. When the entry lacks a value the header still may have one, so sending the
 * form's empty value would overwrite the header's only correct copy — permanently,
 * since every later edit re-bases off the wiped value. The server refreshes a header
 * key only where the request actually supplied it, so omission is the protection.
 *
 * `provides` is a trimmed SCALAR, never {@link parseList}ed; {@link TicketFormValues}
 * says why. It gets the same both-sides-empty omission as its list siblings (see
 * {@link omitProvidesWhenNeverSet}) rather than the general omit-when-unchanged rule
 * those get: a non-empty value is always sent, because the scalar wire shape cannot
 * tell "unchanged" apart from "the user re-typed the same single value" the way a list
 * diff can, and a manifest that stores `provides` as a genuine multi-entry list is a
 * separate, documented open issue this guard does not attempt to fix.
 */
export function toTicketUpdate(values: TicketFormValues, ticket: Ticket): TicketUpdate {
	const dependsOn = parseList(values.dependsOn);
	const provides = values.provides.trim();
	const files = parseList(values.files);

	return {
		title: values.title.trim(),
		...(ticket.track == null ? {} : { track: ticket.track }),
		...(ticket.milestone == null ? {} : { milestone: ticket.milestone }),
		...omitWhenNeverSet('dependsOn', dependsOn, ticket.dependsOn),
		...omitWhenNeverSet('files', files, ticket.files),
		...omitProvidesWhenNeverSet(provides, ticket.provides),
		bodyMarkdown: values.body ?? ''
		// `TicketUpdate` still declares `provides` REQUIRED (stale codegen: the server
		// schema itself defaults it to `""`, `pnpm codegen` against a running backend
		// would drop the `required` entry) — the object above can omit it in the
		// both-empty case, so the return is asserted rather than structurally matched.
	} as TicketUpdate;
}

/**
 * One mirrored list field's entry in the PUT body — or nothing at all, when the field
 * is empty on BOTH sides, where sending it could only destroy something.
 *
 * Omits only when the form and the loaded ticket are both empty, so nothing a user can
 * express is lost: entries they add are sent, and CLEARING a field the ticket really
 * had is still sent, because `loaded` is non-empty there and the clear is deliberate.
 * See {@link toTicketUpdate} for why omission is what protects the `.md` header.
 */
function omitWhenNeverSet<K extends 'dependsOn' | 'files'>(
	key: K,
	value: readonly string[],
	loaded: readonly string[] | undefined
): Partial<Pick<TicketUpdate, K>> {
	const isEmpty = value.length === 0 && (loaded ?? []).length === 0;
	return isEmpty ? {} : ({ [key]: value } as Pick<TicketUpdate, K>);
}

/**
 * `provides`'s own both-sides-empty omit guard — the scalar counterpart of
 * {@link omitWhenNeverSet}.
 *
 * Omits ONLY when the typed value and the loaded ticket's `provides` are both empty.
 * That is the one case omission is safe despite `provides` having no `?` in the
 * generated type: the server defaults an omitted `provides` to `""` (`TicketEdit.
 * provides: str = ""`), which is exactly the value this guard would otherwise have
 * sent — so the manifest entry ends up identical either way, while omitting also
 * keeps the field out of `model_fields_set`, so a `.md` header that independently
 * carries a `provides` this ticket's manifest entry does not is left alone instead of
 * being overwritten with an empty string by an unrelated edit (e.g. a title fix).
 * A non-empty value is always sent — see {@link toTicketUpdate} for why that case is
 * not further protected here.
 */
function omitProvidesWhenNeverSet(
	value: string,
	loaded: readonly string[] | undefined
): Partial<Pick<TicketUpdate, 'provides'>> {
	const isEmpty = value.length === 0 && (loaded ?? []).length === 0;
	return isEmpty ? {} : ({ provides: value } as Pick<TicketUpdate, 'provides'>);
}

/**
 * Build the POST body for one set of form values, in CREATE mode.
 *
 * The deliberate counterpart to {@link toTicketUpdate}, and pointedly SIMPLER: create
 * has NO "omit when never set" guard. That guard exists only to protect a value that
 * already lives on disk (the manifest entry versus the `.md` YAML header) from being
 * wiped by an edit that never meant to touch it — a new ticket has neither, so there
 * is nothing to preserve and every field is sent exactly as typed. A blank
 * `dependsOn` / `files` / `provides` here is a real "no deps / no files / no
 * capability" for the ticket being created, not a signal to leave something alone.
 *
 * Field handling matches the shared {@link TicketFormValues} contract: `dependsOn`
 * and `files` are {@link parseList}ed from their newline textareas into `string[]`;
 * `provides` is a trimmed SCALAR, never {@link parseList}ed (a multi-entry value would
 * collapse to one element on the next read); `id` and `title` are trimmed; and the
 * optional `body` becomes `bodyMarkdown`, defaulted to `''` on the client because the
 * server's `bodyMarkdown` is a REQUIRED field with no default of its own (unlike the
 * adjacent `provides`, which defaults to `""` server-side) — omitting it would 422, so a
 * string must always be sent. `track` / `milestone` are intentionally absent — `TicketForm` does not
 * collect them (see its header note), and both default server-side.
 */
export function toTicketCreate(values: TicketFormValues): TicketCreate {
	return {
		id: values.id.trim(),
		title: values.title.trim(),
		dependsOn: parseList(values.dependsOn),
		provides: values.provides.trim(),
		files: parseList(values.files),
		bodyMarkdown: values.body ?? ''
	};
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
