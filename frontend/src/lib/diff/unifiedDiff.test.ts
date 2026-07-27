import { describe, expect, it } from 'vitest';
import { parseDiffLines } from '$lib/diff/unifiedDiff';

describe('parseDiffLines', () => {
	it('classifies every marker of a unified diff', () => {
		const diff = [
			'--- a/docs/T1.md',
			'+++ b/docs/T1.md',
			'@@ -1,3 +1,3 @@',
			' unchanged',
			'-removed',
			'+added'
		].join('\n');

		expect(parseDiffLines(diff)).toEqual([
			{ text: '--- a/docs/T1.md', kind: 'meta' },
			{ text: '+++ b/docs/T1.md', kind: 'meta' },
			{ text: '@@ -1,3 +1,3 @@', kind: 'hunk' },
			{ text: ' unchanged', kind: 'context' },
			{ text: '-removed', kind: 'del' },
			{ text: '+added', kind: 'add' }
		]);
	});

	// The file headers start with the same characters as the add/del markers, so
	// order of checks is the whole game here.
	it('never reads a +++/--- file header as an added or deleted line', () => {
		const kinds = parseDiffLines('+++ b/x\n--- a/x').map((line) => line.kind);

		expect(kinds).toEqual(['meta', 'meta']);
	});

	// A ticket .md opens and closes its YAML front matter with `---`, so removing
	// one emits `----` — the marker plus the content, not a file header.
	it('reads a removed --- front-matter delimiter as a deleted line', () => {
		const diff = ['--- a/T1.md', '+++ b/T1.md', '@@ -1,2 +1 @@', '----', '-id: T1', '++++'].join(
			'\n'
		);

		expect(parseDiffLines(diff).map((line) => line.kind)).toEqual([
			'meta',
			'meta',
			'hunk',
			'del',
			'del',
			'add'
		]);
	});

	// Inside a hunk the `---`/`+++` shape is content, not a header: removing a
	// line whose own text starts with `-- ` emits `--- `, and adding one that
	// starts with `++ ` emits `+++ `. Only position tells them apart.
	it('reads --- / +++ inside a hunk as deleted and added content lines', () => {
		const diff = [
			'--- a/T1.md',
			'+++ b/T1.md',
			'@@ -1,2 +1,2 @@',
			'--- note',
			'+++ counter',
			'--',
			'++'
		].join('\n');

		expect(parseDiffLines(diff).map((line) => line.kind)).toEqual([
			'meta',
			'meta',
			'hunk',
			'del',
			'add',
			'del',
			'add'
		]);
	});

	it('treats a bare +/- as add/del even with no following text', () => {
		expect(parseDiffLines('+\n-')).toEqual([
			{ text: '+', kind: 'add' },
			{ text: '-', kind: 'del' }
		]);
	});

	it('classifies a blank line and unmarked text as context', () => {
		expect(parseDiffLines('\nno marker')).toEqual([
			{ text: '', kind: 'context' },
			{ text: 'no marker', kind: 'context' }
		]);
	});

	it('returns no lines for an empty diff', () => {
		expect(parseDiffLines('')).toEqual([]);
	});

	it('treats a single trailing newline as a terminator, not a blank line', () => {
		expect(parseDiffLines('+added\n')).toEqual([{ text: '+added', kind: 'add' }]);
		// Two newlines really do mean a trailing blank line.
		expect(parseDiffLines('+added\n\n')).toEqual([
			{ text: '+added', kind: 'add' },
			{ text: '', kind: 'context' }
		]);
	});

	it('splits CRLF diffs without leaving carriage returns in the text', () => {
		expect(parseDiffLines('@@ -1 +1 @@\r\n+added\r\n')).toEqual([
			{ text: '@@ -1 +1 @@', kind: 'hunk' },
			{ text: '+added', kind: 'add' }
		]);
	});
});
