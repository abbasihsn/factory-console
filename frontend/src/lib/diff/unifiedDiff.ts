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
 * The file headers share their marker characters with del/add lines, so a prefix
 * test alone can never separate them: a removed line whose own text is `-- note`
 * arrives as `--- note`, trailing space included. The real rule is POSITIONAL —
 * each `FileDiff.diff` covers ONE file, so `---`/`+++` headers can only appear
 * BEFORE the first `@@` hunk header. `beforeFirstHunk` carries that position, and
 * the prefix constants keep their trailing space so a body line inside the header
 * region (e.g. `----`, a removed front-matter fence) still reads as del/add.
 */
function classifyLine(text: string, beforeFirstHunk: boolean): DiffLineKind {
	if (text.startsWith(HUNK_PREFIX)) return 'hunk';
	if (beforeFirstHunk && (text.startsWith(OLD_FILE_PREFIX) || text.startsWith(NEW_FILE_PREFIX))) {
		return 'meta';
	}
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
 * - The server's diffs are NOT newline-terminated: `write_diff.preview` passes
 *   `lineterm=""` to `difflib.unified_diff` and `"\n".join(...)`s the result, so
 *   there is no trailing segment to drop in production. Dropping one trailing
 *   empty segment is DEFENSIVE, for hand-written or other callers that do
 *   terminate. Interior blank lines are always kept (as `context`).
 * - A trailing `\r` is stripped per line so a CRLF diff classifies and renders
 *   the same as an LF one.
 * - `---`/`+++` are read as file headers only before the first `@@` hunk header,
 *   since one `FileDiff.diff` describes one file (see `classifyLine`).
 */
export function parseDiffLines(diff: string): DiffLine[] {
	if (diff === '') return [];

	const segments = diff.split('\n');
	if (segments.length > 1 && segments[segments.length - 1] === '') {
		segments.pop();
	}

	let beforeFirstHunk = true;
	return segments.map((segment) => {
		const text = segment.endsWith('\r') ? segment.slice(0, -1) : segment;
		const kind = classifyLine(text, beforeFirstHunk);
		if (kind === 'hunk') beforeFirstHunk = false;
		return { text, kind };
	});
}
