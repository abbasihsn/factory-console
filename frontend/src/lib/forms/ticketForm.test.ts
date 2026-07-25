import { describe, expect, it } from 'vitest';
import {
	TICKET_ID_PATTERN,
	parseList,
	serializeList,
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
