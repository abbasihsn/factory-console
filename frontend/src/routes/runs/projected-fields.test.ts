import { describe, expect, it } from 'vitest';

// The page's own SOURCE TEXT, not its rendered output. What is under test here is
// a property of the file — "no key is read out of an artifact payload that is not
// declared in `PROJECTED_FIELDS`" — and a render test cannot see a read that no
// fixture happens to exercise. `?raw` is Vite's verbatim-source import, which is
// how a vitest suite in `src/` reaches a sibling file: `node:fs` would work at
// runtime but this frontend carries no `@types/node`, so it would not type-check.
import pageSource from './+page.svelte?raw';
// The declaration itself, not a transcription of it: a rename or an edit to the
// list is picked up here rather than silently diverging from a second copy.
import { PROJECTED_FIELDS } from './+page.svelte';

// `readString`/`readField` take a `ProjectedField`, so TypeScript already rejects
// an undeclared literal key — this test is the second lock, for the two things the
// type cannot catch: a widened parameter type (someone puts `string` back), and a
// declared field that nothing reads any more (a stale entry the type is happy with
// but which overstates what the view depends on).
/**
 * Every `readString(x, …)` / `readField(x, …)` CALL, with its key argument raw.
 *
 * The `function` lookbehind drops the two declarations, whose second parameter
 * reads `key: ProjectedField` and is not a key at all.
 */
const KEY_READS = /(?<!function )\b(readString|readField)\s*\(\s*[^,()]+,\s*([^),]+)\)/g;

/** The raw second argument of every field read in the source, in order. */
function keyArguments(source: string): string[] {
	return [...source.matchAll(KEY_READS)].map((m) => m[2].trim());
}

/** The literal keys among them — `readField`'s forward of its own `key` aside. */
function literalKeys(source: string): string[] {
	return keyArguments(source)
		.filter((arg) => arg !== 'key')
		.map((arg) => arg.slice(1, -1));
}

describe('the runs view reads only its declared projection', () => {
	it('finds the reads at all', () => {
		// Guards every assertion below against vacuity: a source this file's regex no
		// longer matches would otherwise satisfy every "for each read" loop with zero
		// reads, and the whole suite would pass while checking nothing.
		expect(keyArguments(pageSource).length).toBeGreaterThan(0);
		expect(literalKeys(pageSource).length).toBeGreaterThan(0);
	});

	it('passes only literal keys, or the forwarded `key` parameter, to a field read', () => {
		// A computed key (`readField(a, someVar)`) would put the projection back out
		// of reach of both locks — the type would be satisfied by a `ProjectedField`
		// variable while this file could no longer tell which name was read. The one
		// legitimate non-literal is `readField`'s own forward into `readString`.
		for (const arg of keyArguments(pageSource)) {
			expect(arg === 'key' || /^'[^']+'$/.test(arg)).toBe(true);
		}
	});

	it('reads no artifact field that PROJECTED_FIELDS does not declare', () => {
		for (const key of literalKeys(pageSource)) {
			// Reading a new field means declaring it FIRST, in one place, in the same
			// diff — the whole point of the constant. These names are unverified
			// guesses (see `tests/fixtures/runs/README.md`); what IS verified is that
			// the set of them cannot grow by accident.
			expect(PROJECTED_FIELDS, `undeclared artifact field read: ${key}`).toContain(key);
		}
	});

	it('declares no field that nothing reads', () => {
		// The list is a statement of what this view depends on. A leftover entry
		// claims a dependency that is not there, which is the same overclaim in the
		// other direction.
		const read = new Set(literalKeys(pageSource));
		for (const field of PROJECTED_FIELDS) {
			expect([...read], `declared but never read: ${field}`).toContain(field);
		}
	});

	it('keeps the field readers narrowed to ProjectedField', () => {
		// The compile-time half of the lock. Widening either signature back to
		// `string` would leave this file as the only guard, and a test that can be
		// deleted is a weaker rule than a type error.
		expect(pageSource).toContain(
			'function readString(artifact: ArtifactRead, key: ProjectedField)'
		);
		expect(pageSource).toContain('function readField(artifact: ArtifactRead, key: ProjectedField)');
	});
});
