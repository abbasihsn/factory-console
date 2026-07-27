/**
 * Tiny unified-diff line classifier.
 *
 * The SPA only ever *renders* the unified-diff text the server produced
 * (`FileDiff.diff`), so it needs a classification per line — not a diff engine.
 * Keeping it local avoids adding a diff dependency for a dozen lines of work.
 */

/** How one line of a unified diff should be rendered. */
export type DiffLineKind = 'add' | 'del' | 'hunk' | 'meta' | 'context';

/** One classified line of a unified diff, marker included, newline stripped. */
export interface DiffLine {
	readonly text: string;
	readonly kind: DiffLineKind;
}

/**
 * Shape of the `---` / `+++` file headers.
 *
 * The marker must be followed by whitespace or end-of-line, because a *content*
 * line beginning with `--` or `++` carries its own add/del marker on top and so
 * starts with the same three characters: deleting a ticket's `---` front-matter
 * delimiter emits `----`, which is a deleted line, not a header.
 *
 * Shape alone is not enough to identify a header, because a content line can
 * also carry the whitespace — removing `-- note` emits `--- note`. See
 * `classifyLine`, which pairs this with the header's *position*.
 */
const FILE_HEADER = /^(?:\+\+\+|---)(?:\s|$)/;

/**
 * Classify one line by its leading marker and its position in the diff.
 *
 * `inHunk` is what disambiguates a header from a content line that merely looks
 * like one: file headers only ever precede the first `@@`, so once a hunk has
 * started every `---`/`+++` line is a removed/added line whose own text begins
 * with `--`/`++`. Headers are still checked before the single-character
 * `+` / `-` markers, otherwise they would read as an added / deleted line.
 */
function classifyLine(text: string, inHunk: boolean): DiffLineKind {
	if (!inHunk && FILE_HEADER.test(text)) return 'meta';
	if (text.startsWith('@@')) return 'hunk';
	if (text.startsWith('+')) return 'add';
	if (text.startsWith('-')) return 'del';
	return 'context';
}

/**
 * Split a unified diff into classified lines, in order.
 *
 * Handles both `\n` and `\r\n` endings. A single trailing newline is a line
 * *terminator*, not an empty last line, so it does not yield a stray blank row;
 * an empty diff yields no lines at all.
 */
export function parseDiffLines(diff: string): DiffLine[] {
	if (diff === '') return [];
	const lines = diff.split(/\r?\n/);
	if (lines[lines.length - 1] === '') lines.pop();
	// Latches on the first `@@`: everything after it is hunk body, where a
	// `---`/`+++` line is content, not a header.
	let inHunk = false;
	return lines.map((text) => {
		const kind = classifyLine(text, inHunk);
		if (kind === 'hunk') inHunk = true;
		return { text, kind };
	});
}
