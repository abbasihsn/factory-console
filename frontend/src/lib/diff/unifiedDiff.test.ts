import { describe, expect, it } from 'vitest';
import { parseDiffLines, type DiffLineKind } from '$lib/diff/unifiedDiff';

/** A realistic unified diff for one modified file, as the server emits it. */
const MODIFY_DIFF = [
	'--- a/docs/planning/tickets/v2/T69.md',
	'+++ b/docs/planning/tickets/v2/T69.md',
	'@@ -1,4 +1,5 @@',
	' # T69',
	'-old title',
	'+new title',
	'+extra line',
	' trailing context'
].join('\n');

/** Just the kinds, in order — the shape most assertions care about. */
function kindsOf(diff: string): DiffLineKind[] {
	return parseDiffLines(diff).map((line) => line.kind);
}

describe('parseDiffLines', () => {
	it('classifies meta, hunk, context, del and add lines of a real diff in order', () => {
		expect(kindsOf(MODIFY_DIFF)).toEqual([
			'meta',
			'meta',
			'hunk',
			'context',
			'del',
			'add',
			'add',
			'context'
		]);
	});

	it('keeps each line verbatim, marker included', () => {
		const lines = parseDiffLines(MODIFY_DIFF);

		expect(lines[0].text).toBe('--- a/docs/planning/tickets/v2/T69.md');
		expect(lines[4].text).toBe('-old title');
		expect(lines[5].text).toBe('+new title');
	});

	it('reads the file headers as meta, NOT as add/del content', () => {
		// `+++`/`---` share a first character with add/del, so precedence decides.
		expect(kindsOf('--- a/x\n+++ b/x')).toEqual(['meta', 'meta']);
	});

	it('treats a bare - or + as a removed or added empty line', () => {
		expect(kindsOf('-\n+')).toEqual(['del', 'add']);
	});

	it('classifies the no-newline-at-eof note as meta', () => {
		expect(kindsOf('+last line\n\\ No newline at end of file')).toEqual(['add', 'meta']);
	});

	it('returns no lines for an empty diff', () => {
		expect(parseDiffLines('')).toEqual([]);
	});

	it('drops the terminating newline instead of reporting a trailing blank line', () => {
		expect(parseDiffLines('@@ -1 +1 @@\n')).toEqual([{ text: '@@ -1 +1 @@', kind: 'hunk' }]);
	});

	it('keeps interior blank lines as context', () => {
		expect(kindsOf(' a\n\n b\n')).toEqual(['context', 'context', 'context']);
	});

	it('strips the CR of a CRLF diff so it classifies and renders like LF', () => {
		expect(parseDiffLines('@@ -1 +1 @@\r\n+added\r\n')).toEqual([
			{ text: '@@ -1 +1 @@', kind: 'hunk' },
			{ text: '+added', kind: 'add' }
		]);
	});

	it('classifies an unmarked line (a create-diff banner) as context', () => {
		expect(kindsOf('diff --git a/x b/x')).toEqual(['context']);
	});
});
