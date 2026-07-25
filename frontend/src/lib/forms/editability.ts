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
 * `server/factory_console/file_adapter/write_gate.py`. All other states
 * (`'in-flight'`, `'ready'`, `'merged'`) are read-only because a factory lane
 * owns the ticket once it leaves `todo`.
 */
export function isEditable(runState: RunState): boolean {
	return runState === 'todo' || runState === 'unknown';
}
