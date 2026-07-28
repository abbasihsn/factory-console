import { describe, expect, it } from 'vitest';
import type { Ticket } from '$lib/api';
import {
	TICKET_ID_PATTERN,
	parseList,
	serializeList,
	toTicketUpdate,
	validateTicketForm,
	type TicketFormValues
} from './ticketForm';

/** A fully-valid create payload; individual tests override single fields. */
function values(overrides: Partial<TicketFormValues> = {}): TicketFormValues {
	return {
		id: 'T67',
		title: 'A valid title',
		dependsOn: '',
		provides: '',
		files: '',
		...overrides
	};
}

describe('TICKET_ID_PATTERN', () => {
	it.each(['T67', 'T01', 'abc', 'ABC123', 'a_b', 'a.b', 'a-b', 'a.b_c-d', '9'])(
		'accepts valid id %j',
		(id) => {
			expect(TICKET_ID_PATTERN.test(id)).toBe(true);
		}
	);

	it.each([
		['contains a space', 'T 67'],
		['leading space', ' T67'],
		['trailing space', 'T67 '],
		['contains a slash', 'a/b'],
		['contains a backslash', 'a\\b'],
		['empty string', ''],
		['contains a colon', 'a:b'],
		['contains a hash', 'a#b'],
		['contains a plus', 'a+b']
	])('rejects id that %s (%j)', (_label, id) => {
		expect(TICKET_ID_PATTERN.test(id)).toBe(false);
	});
});

describe('parseList / serializeList', () => {
	it('parseList splits on newlines, trims, and drops empties', () => {
		expect(parseList('a\n b \n\n\tc\n')).toEqual(['a', 'b', 'c']);
	});

	it('parseList of an empty string is an empty list', () => {
		expect(parseList('')).toEqual([]);
		expect(parseList('\n\n  \n')).toEqual([]);
	});

	it('serializeList joins with newlines', () => {
		expect(serializeList(['a', 'b', 'c'])).toBe('a\nb\nc');
		expect(serializeList([])).toBe('');
	});

	it.each([[[]], [['a']], [['a', 'b', 'c']], [['T01', 'T02', 'T03']]])(
		'round-trips already-clean list %j',
		(xs) => {
			expect(parseList(serializeList(xs))).toEqual(xs);
		}
	);
});

describe('validateTicketForm', () => {
	it('accepts a fully-valid create form (empty error map)', () => {
		expect(validateTicketForm(values(), { mode: 'create' })).toEqual({});
	});

	it('accepts a fully-valid edit form (empty error map)', () => {
		expect(validateTicketForm(values(), { mode: 'edit' })).toEqual({});
	});

	it('requires title in create mode', () => {
		expect(validateTicketForm(values({ title: '' }), { mode: 'create' })).toHaveProperty('title');
		expect(validateTicketForm(values({ title: '   ' }), { mode: 'create' })).toHaveProperty(
			'title'
		);
	});

	it('requires title in edit mode', () => {
		expect(validateTicketForm(values({ title: '' }), { mode: 'edit' })).toHaveProperty('title');
	});

	it('requires id in create mode', () => {
		const errors = validateTicketForm(values({ id: '' }), { mode: 'create' });
		expect(errors).toHaveProperty('id');
	});

	it('does NOT require id in edit mode (id is fixed by the route)', () => {
		const errors = validateTicketForm(values({ id: '' }), { mode: 'edit' });
		expect(errors).not.toHaveProperty('id');
		expect(errors).toEqual({});
	});

	it.each(['T 67', 'a/b', 'a:b', 'bad id'])('rejects a malformed id %j in create mode', (id) => {
		expect(validateTicketForm(values({ id }), { mode: 'create' })).toHaveProperty('id');
	});

	it.each(['T 67', 'a/b', 'a:b'])(
		'rejects a malformed id %j even in edit mode (if provided)',
		(id) => {
			expect(validateTicketForm(values({ id }), { mode: 'edit' })).toHaveProperty('id');
		}
	);

	it('reports both id and title errors at once', () => {
		const errors = validateTicketForm(values({ id: 'a b', title: '' }), { mode: 'create' });
		expect(errors).toHaveProperty('id');
		expect(errors).toHaveProperty('title');
	});
});

describe('toTicketUpdate', () => {
	/** A loaded ticket carrying both mirrored fields; tests override single fields. */
	function ticket(overrides: Partial<Ticket> = {}): Ticket {
		return {
			id: 'T67',
			title: 'As loaded',
			status: 'todo',
			track: 'frontend',
			milestone: 'v2',
			runState: 'todo',
			dependsOn: [],
			provides: [],
			files: [],
			filePath: '/docs/planning/tickets/v2/T67.md',
			bodyMarkdown: '',
			bodyHtml: '',
			raw: {},
			...overrides
		};
	}

	it('echoes track/milestone the loaded ticket carries', () => {
		const body = toTicketUpdate(values({ title: 'Renamed' }), ticket());
		expect(body.track).toBe('frontend');
		expect(body.milestone).toBe('v2');
	});

	// REGRESSION: `track: ticket.track ?? null` sent an explicit null, and the server
	// treats an explicit null as "clear it" while an OMITTED key changes nothing. Since
	// Ticket.track comes from the manifest entry alone, a ticket whose manifest lacks
	// the field but whose .md header carries one had its header value destroyed on any
	// ordinary edit. The key must be ABSENT, not null.
	it('OMITS track entirely when the loaded ticket has none', () => {
		const body = toTicketUpdate(values(), ticket({ track: null }));
		expect(body).not.toHaveProperty('track');
		expect(Object.keys(body)).not.toContain('track');
	});

	it('OMITS milestone entirely when the loaded ticket has none', () => {
		const body = toTicketUpdate(values(), ticket({ milestone: null }));
		expect(body).not.toHaveProperty('milestone');
	});

	it('OMITS both when the loaded ticket is missing the keys altogether', () => {
		// `Ticket` declares both keys optional, so a manifest entry lacking them yields
		// an object with no such properties — not merely null-valued ones.
		const bare = { ...ticket() } as { track?: string | null; milestone?: string | null };
		delete bare.track;
		delete bare.milestone;
		const body = toTicketUpdate(values(), bare as unknown as Ticket);
		expect(body).not.toHaveProperty('track');
		expect(body).not.toHaveProperty('milestone');
	});

	it('never sends null for either mirrored field', () => {
		for (const t of [ticket({ track: null }), ticket({ milestone: null }), ticket()]) {
			const body = toTicketUpdate(values(), t);
			expect(body.track).not.toBeNull();
			expect(body.milestone).not.toBeNull();
		}
	});

	it('trims the title and provides, and parses the list fields', () => {
		const body = toTicketUpdate(
			values({
				title: '  Spaced  ',
				provides: '  a cap  ',
				dependsOn: 'T1\n\n T2 ',
				files: 'a\nb'
			}),
			ticket()
		);
		expect(body.title).toBe('Spaced');
		expect(body.provides).toBe('a cap');
		expect(body.dependsOn).toEqual(['T1', 'T2']);
		expect(body.files).toEqual(['a', 'b']);
	});

	it('sends an empty bodyMarkdown when the form has no body', () => {
		expect(toTicketUpdate(values(), ticket()).bodyMarkdown).toBe('');
	});

	// Same failure class as track/milestone above, for the three fields the form DOES
	// edit. They are mirrored into the `.md` header, but `Ticket.dependsOn/provides/
	// files` are read from the manifest entry alone — so a ticket whose manifest lacks
	// one while its header carries a real value seeds the form empty, and sending the
	// empty value would wipe the header's only copy.
	describe('mirrored list fields the manifest may not carry', () => {
		const EMPTY_FORM = { dependsOn: '', provides: '', files: '' };

		it.each(['dependsOn', 'provides', 'files'] as const)(
			'OMITS %s when it is empty on both the form and the loaded ticket',
			(key) => {
				const body = toTicketUpdate(values(EMPTY_FORM), ticket());
				expect(body).not.toHaveProperty(key);
			}
		);

		it('still sends what the user actually entered', () => {
			const body = toTicketUpdate(
				values({ dependsOn: 'T1', provides: 'a cap', files: 'a.ts' }),
				ticket()
			);
			expect(body.dependsOn).toEqual(['T1']);
			expect(body.provides).toBe('a cap');
			expect(body.files).toEqual(['a.ts']);
		});

		// The omission must never swallow a deliberate CLEAR: the loaded ticket has the
		// value, so emptying the field is a real edit and has to reach the server.
		it('sends the empty value when the loaded ticket really had one', () => {
			const body = toTicketUpdate(
				values(EMPTY_FORM),
				ticket({ dependsOn: ['T1'], provides: ['a cap'], files: ['a.ts'] })
			);
			expect(body.dependsOn).toEqual([]);
			expect(body.provides).toBe('');
			expect(body.files).toEqual([]);
		});
	});
});
