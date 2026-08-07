import { describe, expect, it } from 'vitest';
import { switchTarget } from './switchTarget';

// Every route the app actually has (see `src/routes/`), so a route added without
// a decision about the switch shows up here as a missing case rather than as a
// surprise redirect.
const STAY_PUT = [
	'/',
	'/graph',
	'/roadmap',
	'/search',
	'/spend',
	'/runs',
	// `/tickets/new` is a STATIC route, so its segment is a verb and not a ticket
	// id — the form describes the new project as well as it did the old one.
	'/tickets/new'
];

describe('switchTarget', () => {
	it.each(STAY_PUT)('stays put on %s (no ticket id in the URL)', (pathname) => {
		expect(switchTarget(pathname)).toBeNull();
	});

	it('goes home from a ticket detail route', () => {
		expect(switchTarget('/tickets/T31')).toBe('/');
	});

	it('goes home from a ticket deps route', () => {
		expect(switchTarget('/tickets/T31/deps')).toBe('/');
	});

	it('goes home from a ticket route with a trailing slash', () => {
		expect(switchTarget('/tickets/T31/')).toBe('/');
		expect(switchTarget('/tickets/T31/deps/')).toBe('/');
	});

	it('goes home from a ticket id that merely starts with "new"', () => {
		// The exclusion is the static route `/tickets/new` itself, not every id
		// beginning with those letters.
		expect(switchTarget('/tickets/newsletter')).toBe('/');
	});

	it('stays put on the new-ticket form even with a trailing slash', () => {
		expect(switchTarget('/tickets/new/')).toBeNull();
	});

	it('stays put on an unknown path rather than inventing a redirect', () => {
		// A 404 is the router's business; the switch only decides whether the URL
		// still means something under the new project.
		expect(switchTarget('/nope')).toBeNull();
	});
});
