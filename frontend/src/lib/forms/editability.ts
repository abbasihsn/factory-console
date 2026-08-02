/**
 * PURE write-gate predicates — no Svelte, no I/O.
 *
 * Client-side mirror of the server write-gate so the UI can disable writes the
 * server would reject anyway. Defense in depth only — the server enforces the real
 * gate.
 *
 * There are TWO predicates because the server has two allowlists, not one:
 * `MUTABLE_STATES` for edit and the wider `DELETABLE_STATES` for delete. Mirroring
 * only the first would disable the Delete button for `'absent'`, which is exactly
 * the "the console cannot un-create its own ticket" hole the server's
 * `ensure_deletable` exists to close (T80 amendment, gap 2) — the UI would simply
 * move the refusal from a 409 to a greyed-out button.
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
 * `'failed'`/`'needs_human'`, `'absent'` (a resolved run-state source that
 * simply does not list the ticket — distinct from `'unknown'`, which stays
 * editable), and `'unreadable'` (a run-state source that is THERE and could not be
 * read at all, which unlike `'absent'` is refused by {@link isDeletable} too).
 * Being an ALLOWLIST is the point: a state the factory adds is
 * read-only here the moment it appears in the generated type, with no code
 * change and no window where the UI offers an edit the server refuses. A test
 * pins that for each read-only state.
 */
export function isEditable(runState: RunState): boolean {
	return runState === 'todo' || runState === 'unknown';
}

/**
 * Whether a ticket in the given run-state may be DELETED.
 *
 * Everything {@link isEditable} allows, PLUS `'absent'`. MIRRORS the server's
 * `DELETABLE_STATES = (*MUTABLE_STATES, RunState.absent)` — source of truth:
 * `server/factory_console/file_adapter/write_gate.py`. Deliberately a separate
 * predicate rather than a widened `isEditable`, exactly as the server keeps two
 * tuples: editing a ticket a resolved run-state source does not list stays refused,
 * while deleting it is permitted, because create is ungated and a ticket the console
 * just created resolves `'absent'` the moment the project has a populated run-state
 * source. Deleting a ticket the run-state does not track cannot orphan a run-state
 * entry, so nothing the factory owns is at risk.
 *
 * The widening stops at `'absent'`. `'unreadable'` — a run-state source that is there
 * and could not be read — is refused here too, mirroring the server's
 * `DELETABLE_STATES`, because it proves nothing about whether the factory tracks the
 * ticket: the entry saying a lane owns it may be exactly what could not be read
 * (T80 amendment 2).
 *
 * Still an ALLOWLIST, for the same reason `isEditable` is: a state the factory adds
 * is undeletable here the moment it appears in the generated type.
 */
export function isDeletable(runState: RunState): boolean {
	return isEditable(runState) || runState === 'absent';
}
