/**
 * PURE editability predicate — no Svelte, no I/O.
 *
 * Client-side mirror of the server write-gate so the UI can disable editing for
 * states the server would reject anyway. Defense in depth only — the server
 * enforces the real gate.
 */
import type { RunState } from '$lib/api';

/**
 * Whether a ticket in the given run-state may be edited.
 *
 * Returns `true` ONLY for `'todo'` and `'unknown'`. MIRRORS the server write-gate
 * `MUTABLE_STATES = (RunState.todo, RunState.unknown)` — source of truth:
 * `server/factory_console/file_adapter/write_gate.py`. Every other state is
 * read-only because a factory lane owns the ticket once it leaves `todo` — the
 * marker directory's `'in-flight'`/`'ready'`/`'merged'`, the factory
 * run-state.json's `'in_progress'`/`'in_part'`/`'in_submilestone'`/`'flagged'`/
 * `'failed'`/`'needs_human'`, and `'absent'` (a resolved run-state source that
 * simply does not list the ticket — distinct from `'unknown'`, which stays
 * editable). Being an ALLOWLIST is the point: a state the factory adds is
 * read-only here the moment it appears in the generated type, with no code
 * change and no window where the UI offers an edit the server refuses. A test
 * pins that for each read-only state.
 */
export function isEditable(runState: RunState): boolean {
	return runState === 'todo' || runState === 'unknown';
}
