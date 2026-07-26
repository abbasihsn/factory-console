/**
 * PURE unified-diff line classifier — no Svelte, no I/O, no dependencies.
 *
 * The server hands each planned/applied file change to the SPA as unified-diff
 * TEXT (`FileDiff.diff`). Rendering it only needs to know what KIND each line is
 * so it can be colored; it does not need to reconstruct the two file versions.
 * A hand-rolled classifier keeps that cheap and avoids adding a diff library to
 * `package.json` (see the T69 ticket rationale).
 */

/** What one unified-diff line represents. */
export type DiffLineKind = 'add' | 'del' | 'hunk' | 'meta' | 'context';

/** One classified line of a unified diff, marker included in `text`. */
export interface DiffLine {
	/** The line as it should be displayed, INCLUDING its leading marker. */
	readonly text: string;
	readonly kind: DiffLineKind;
}

/** `@@ -1,4 +1,6 @@` — a hunk header. */
const HUNK_PREFIX = '@@';

/**
 * `--- a/path` / `+++ b/path` — the two file headers. The TRAILING SPACE is part
 * of the prefix: `difflib.unified_diff` always emits the marker, a space, then
 * the filename, so requiring it keeps content lines out of these constants.
 */
const OLD_FILE_PREFIX = '--- ';
const NEW_FILE_PREFIX = '+++ ';

/** `\ No newline at end of file` — a note about the file, not file content. */
const NOTE_PREFIX = '\\';

/**
 * Classify one already-newline-stripped diff line.
 *
 * The file headers share a first character with a del/add line, so they are
 * tested FIRST — but their prefixes include the space that follows the marker in
 * a real header, so they no longer swallow ordinary content. A removed `---`
 * front-matter fence (diff line `----`) reads as `del`, and an added one `add`.
 */
function classifyLine(text: string): DiffLineKind {
	if (text.startsWith(HUNK_PREFIX)) return 'hunk';
	if (text.startsWith(OLD_FILE_PREFIX) || text.startsWith(NEW_FILE_PREFIX)) return 'meta';
	if (text.startsWith(NOTE_PREFIX)) return 'meta';
	// A bare '+' / '-' is an added / removed EMPTY line — still add/del.
	if (text.startsWith('+')) return 'add';
	if (text.startsWith('-')) return 'del';
	return 'context';
}

/**
 * Split a unified diff into classified display lines.
 *
 * Decisions worth knowing at the call site:
 * - An empty diff yields NO lines (`[]`), so a caller can treat an empty array
 *   as "nothing to show" without inspecting the string itself.
 * - A unified diff is newline-TERMINATED, so one trailing empty segment from the
 *   split is the terminator and is dropped — it is not a blank line. Interior
 *   blank lines are kept (as `context`).
 * - A trailing `\r` is stripped per line so a CRLF diff classifies and renders
 *   the same as an LF one.
 */
export function parseDiffLines(diff: string): DiffLine[] {
	if (diff === '') return [];

	const segments = diff.split('\n');
	if (segments.length > 1 && segments[segments.length - 1] === '') {
		segments.pop();
	}

	return segments.map((segment) => {
		const text = segment.endsWith('\r') ? segment.slice(0, -1) : segment;
		return { text, kind: classifyLine(text) };
	});
}
