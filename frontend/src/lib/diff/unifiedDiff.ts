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
 * Classify one line by its leading marker.
 *
 * File headers (`+++` / `---`) are checked before the single-character `+` / `-`
 * markers, otherwise they would read as an added / deleted line.
 */
function classifyLine(text: string): DiffLineKind {
	if (text.startsWith('+++') || text.startsWith('---')) return 'meta';
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
	return lines.map((text) => ({ text, kind: classifyLine(text) }));
}
