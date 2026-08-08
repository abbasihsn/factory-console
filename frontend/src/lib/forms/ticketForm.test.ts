import { describe, expect, it } from 'vitest';
import {
	TICKET_ID_PATTERN,
	parseList,
	serializeList,
	toTicketCreate,
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
		context: 'Why this ticket exists.',
		approach: 'Create the module, then wire it up.',
		criticalFiles: 'src/a.ts',
		interfaceData: 'N/A',
		verificationCommands: 'pnpm test',
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

	// The five content fields mirror `schemas/ticket.schema.json`, where every one but
	// `notes` is required. The mirror is defense in depth — the server is the real gate —
	// but a form that let them through would send a request guaranteed to 422.
	describe('the v3 content fields', () => {
		it.each(['context', 'approach', 'interfaceData'] as const)(
			'requires %s in both modes',
			(field) => {
				for (const mode of ['create', 'edit'] as const) {
					expect(validateTicketForm(values({ [field]: '' }), { mode })).toHaveProperty(field);
					expect(validateTicketForm(values({ [field]: '   ' }), { mode })).toHaveProperty(field);
				}
			}
		);

		// Whitespace specifically: a textarea holding only blank lines passes "did you type
		// something?" and answers nothing, which is exactly the shape `minItems: 1` rejects.
		it.each(['criticalFiles', 'verificationCommands'] as const)(
			'requires %s to parse to at least one entry, not merely to be non-empty',
			(field) => {
				expect(validateTicketForm(values({ [field]: '' }), { mode: 'create' })).toHaveProperty(
					field
				);
				expect(
					validateTicketForm(values({ [field]: '\n  \n\n' }), { mode: 'create' })
				).toHaveProperty(field);
				expect(
					validateTicketForm(values({ [field]: 'one' }), { mode: 'create' })
				).not.toHaveProperty(field);
			}
		);

		it('does NOT require verificationNotes — the one optional field in the schema', () => {
			expect(validateTicketForm(values({ verificationNotes: '' }), { mode: 'create' })).toEqual({});
			expect(validateTicketForm(values(), { mode: 'create' })).toEqual({});
		});

		// The messages say WHY, not what: these two are the fields a user is most likely to
		// leave thin, and "at least one entry" does not explain what breaks when they do.
		it('explains what an empty criticalFiles costs', () => {
			const errors = validateTicketForm(values({ criticalFiles: '' }), { mode: 'create' });
			expect(errors.criticalFiles).toContain('overlap filter');
		});

		it('explains what an empty verificationCommands costs', () => {
			const errors = validateTicketForm(values({ verificationCommands: '' }), { mode: 'create' });
			expect(errors.verificationCommands).toContain('not a pass');
		});
	});
});

describe('toTicketUpdate', () => {
	it('trims the title and provides, and parses the list fields', () => {
		const body = toTicketUpdate(
			values({
				title: '  Spaced  ',
				provides: '  a cap  ',
				dependsOn: 'T1\n\n T2 ',
				criticalFiles: 'a\nb'
			})
		);

		expect(body.title).toBe('Spaced');
		expect(body.provides).toBe('a cap');
		expect(body.dependsOn).toEqual(['T1', 'T2']);
		expect(body.criticalFiles).toEqual(['a', 'b']);
	});

	it('sends all five content fields', () => {
		const body = toTicketUpdate(values());

		expect(body.context).toBe('Why this ticket exists.');
		expect(body.approach).toBe('Create the module, then wire it up.');
		expect(body.criticalFiles).toEqual(['src/a.ts']);
		expect(body.interfaceData).toBe('N/A');
		expect(body.verificationCommands).toEqual(['pnpm test']);
	});

	// `track`/`milestone` are still omitted, and for a reason that SURVIVED the v3 change
	// while the other omissions did not: this form does not collect them, so sending
	// anything would be inventing a value. The server refreshes a field only where the
	// request supplied it.
	it.each(['track', 'milestone'] as const)('OMITS %s, which the form never collects', (key) => {
		const body = toTicketUpdate(values());
		expect(body).not.toHaveProperty(key);
		expect(Object.keys(body)).not.toContain(key);
	});

	// THE OMIT-WHEN-NEVER-SET GUARD IS GONE, and its absence is asserted rather than left
	// to be inferred from a missing test. It protected a value that lived in TWO places —
	// the manifest entry and the ticket .md's YAML header — from an edit meant to touch
	// neither. A v3 ticket has no header: every field lives in exactly one file, so an
	// empty form field is an ordinary edit and must reach the server like any other.
	it('sends an emptied dependsOn rather than omitting it', () => {
		const body = toTicketUpdate(values({ dependsOn: '' }));
		expect(body).toHaveProperty('dependsOn');
		expect(body.dependsOn).toEqual([]);
	});

	it('sends an emptied provides rather than omitting it', () => {
		const body = toTicketUpdate(values({ provides: '' }));
		expect(body).toHaveProperty('provides');
		expect(body.provides).toBe('');
	});

	// The one optional content field: absent, not present-and-empty. A key
	// present-and-empty is a different document — it shows as an added line in the diff of
	// every ticket that has no notes, and claims the author answered a question they did not.
	it('OMITS verificationNotes when blank and sends it when written', () => {
		expect(toTicketUpdate(values({ verificationNotes: '   ' }))).not.toHaveProperty(
			'verificationNotes'
		);
		expect(toTicketUpdate(values({ verificationNotes: ' needs DATABASE_URL ' }))).toHaveProperty(
			'verificationNotes',
			'needs DATABASE_URL'
		);
	});

	// The v2 write surface is gone from the wire, not merely unused by this function.
	it.each(['bodyMarkdown', 'frontMatter', 'files'] as const)(
		'never sends the retired v2 key %s',
		(key) => {
			expect(Object.keys(toTicketUpdate(values()))).not.toContain(key);
		}
	);
});

describe('toTicketCreate', () => {
	it('trims id and title and sends the whole v3 shape', () => {
		const body = toTicketCreate(values({ id: '  T99  ', title: '  New ticket  ' }));

		expect(body).toEqual({
			id: 'T99',
			title: 'New ticket',
			dependsOn: [],
			provides: '',
			context: 'Why this ticket exists.',
			approach: 'Create the module, then wire it up.',
			criticalFiles: ['src/a.ts'],
			interfaceData: 'N/A',
			verificationCommands: ['pnpm test']
		});
	});

	it('parses the three list fields from newline text, trimming and dropping blanks', () => {
		const body = toTicketCreate(
			values({
				dependsOn: 'T1\n  T2  \n\nT3',
				criticalFiles: 'a.ts\n\n b.ts ',
				verificationCommands: 'pnpm test\n  ruff check . '
			})
		);

		expect(body.dependsOn).toEqual(['T1', 'T2', 'T3']);
		expect(body.criticalFiles).toEqual(['a.ts', 'b.ts']);
		expect(body.verificationCommands).toEqual(['pnpm test', 'ruff check .']);
	});

	it('keeps provides as a trimmed SCALAR — a multi-line value is never split into a list', () => {
		const body = toTicketCreate(values({ provides: '  edit affordances\nsecond line  ' }));

		// parseList would collapse this to ['edit affordances', 'second line']; the scalar
		// contract keeps the internal newline and only trims the ends.
		expect(body.provides).toBe('edit affordances\nsecond line');
	});

	// Create and edit are now the same shape plus an id, which is what the server's own
	// DTOs look like (`TicketDraft` is `TicketEdit` plus an id). They used to diverge
	// because edit carried the header-protection guard create had no need for.
	it('differs from toTicketUpdate by exactly the id', () => {
		const created = toTicketCreate(values());
		const updated = toTicketUpdate(values());

		const { id, ...withoutId } = created;
		expect(id).toBe('T67');
		expect(withoutId).toEqual(updated);
	});
});
