import { readFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { DB_STATE_FILE, HOME_CONSOLE_DB_PATH, PID_FILE } from './global-setup';

// The prefix global-setup passes to `mkdtempSync` for its per-run temp DB dir —
// duplicated here (not imported) because it must be checked against whatever
// `dbDir` a possibly-stale or malformed state file claims, not trusted from the
// same source that wrote it.
const DB_DIR_PREFIX = 'factory-console-e2e-db-';

/**
 * Validate a `dbDir` claim from `DB_STATE_FILE` before it is ever handed to a
 * recursive delete.
 *
 * `DB_STATE_FILE` lives at a fixed, guessable path and is read back in a
 * SEPARATE process with no schema enforcement beyond `JSON.parse` — a stale
 * file left by an aborted run, or one from a different checkout sharing the
 * same OS temp dir, would otherwise feed `rmSync` a directory this run never
 * created. Requiring it to resolve strictly under the OS temp dir AND carry
 * this run's own `mkdtempSync` prefix rejects both a wrong-but-real directory
 * and a malformed/missing value (which would otherwise reach `rmSync(undefined)`
 * as a confusing `TypeError` instead of a stated reason).
 */
function validatedDbDir(value: unknown): string {
	if (typeof value !== 'string' || value.length === 0) {
		throw new Error(
			`factory-console e2e teardown: ${DB_STATE_FILE} named a dbDir that is not a ` +
				`non-empty string (${JSON.stringify(value)}) — refusing to delete it.`
		);
	}
	const resolved = path.resolve(value);
	const tmpRoot = path.resolve(tmpdir());
	const withinTmp = resolved === tmpRoot || resolved.startsWith(tmpRoot + path.sep);
	if (!withinTmp || !path.basename(resolved).startsWith(DB_DIR_PREFIX)) {
		throw new Error(
			`factory-console e2e teardown: ${DB_STATE_FILE} named dbDir "${resolved}", which is ` +
				`not one of this harness's own temp DB dirs (expected it under ${tmpRoot} with the ` +
				`"${DB_DIR_PREFIX}" prefix) — refusing to delete it.`
		);
	}
	return resolved;
}

// Poll cadence and total grace given to a clean SIGTERM shutdown (uvicorn drains
// and exits 0) before escalating to SIGKILL.
const KILL_POLL_INTERVAL_MS = 100;
const KILL_TIMEOUT_MS = 5_000;

// `process.kill(pid, 0)` sends no signal — it just probes existence, throwing
// ESRCH once the process is gone.
function isAlive(pid: number): boolean {
	try {
		process.kill(pid, 0);
		return true;
	} catch (err) {
		return (err as NodeJS.ErrnoException).code !== 'ESRCH';
	}
}

function sleep(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

async function globalTeardown(): Promise<void> {
	// The leak guard and this run's own cleanup must run on EVERY exit path out of
	// `killConsoleIfAlive` — including the early returns for "no PID file" and
	// "already gone" (ESRCH, e.g. the console crashed) — not only the one where
	// the kill sequence ran to completion. Skipping it on those paths left the
	// temp DB dir and the state file behind in the OS temp dir, unbounded, on
	// every run where the child died before teardown got to it.
	try {
		await killConsoleIfAlive();
	} finally {
		await assertRealStoreUntouchedAndCleanUp();
	}
}

async function killConsoleIfAlive(): Promise<void> {
	let pid: number;
	try {
		pid = Number.parseInt(readFileSync(PID_FILE, 'utf8').trim(), 10);
	} catch {
		// No PID file — setup never recorded a child, so nothing to tear down.
		return;
	}
	if (!Number.isInteger(pid)) {
		rmSync(PID_FILE, { force: true });
		return;
	}

	try {
		process.kill(pid, 'SIGTERM');
	} catch (err) {
		// Already gone: swallow ESRCH, surface anything else.
		if ((err as NodeJS.ErrnoException).code !== 'ESRCH') throw err;
		rmSync(PID_FILE, { force: true });
		return;
	}

	// Wait for the graceful shutdown; force-kill if it overstays so no orphaned
	// factory-console lingers.
	const deadline = Date.now() + KILL_TIMEOUT_MS;
	while (isAlive(pid) && Date.now() < deadline) {
		await sleep(KILL_POLL_INTERVAL_MS);
	}
	if (isAlive(pid)) {
		try {
			process.kill(pid, 'SIGKILL');
		} catch (err) {
			if ((err as NodeJS.ErrnoException).code !== 'ESRCH') throw err;
		}
	}

	rmSync(PID_FILE, { force: true });
}

// The protection this whole harness exists to prove: with the child now dead,
// confirm ~/.factory-console/console.db was not created (or, if it already
// existed, that it was not written to) before removing this run's own temp DB
// dir. Throwing here fails the teardown itself — a leak into the developer's
// real store is a harness regression, not a thing to clean up quietly and move
// on from. Cleanup runs in a `finally` so a tripped guard still reaps this run's
// OWN artifacts instead of leaking them on top of failing loudly.
async function assertRealStoreUntouchedAndCleanUp(): Promise<void> {
	let state: { dbDir: unknown; homeDbStat: { mtimeMs: number; size: number } | null };
	try {
		state = JSON.parse(readFileSync(DB_STATE_FILE, 'utf8'));
	} catch {
		// No state file — global-setup never got far enough to write one (it threw
		// before doing so), so there is nothing recorded to check or clean up.
		return;
	}

	let dbDir: string;
	try {
		dbDir = validatedDbDir(state.dbDir);
	} finally {
		// This run's own bookkeeping, reclaimed regardless of whether `dbDir`
		// validates — a rejected value must not also leave a stale state file
		// behind for the NEXT run to trip over.
		rmSync(DB_STATE_FILE, { force: true });
	}

	try {
		// The artifact itself, not the directory: a directory's mtime only moves
		// when an entry is added or removed, so comparing it misses a write to an
		// already-existing `console.db` (false negative) and trips on any
		// unrelated tool that merely touches the directory, e.g. the developer's
		// own console running concurrently (false positive). `size` alongside
		// `mtimeMs` catches a same-tick write a coarse mtime clock could alias.
		let currentDbStat: { mtimeMs: number; size: number } | null;
		try {
			const stat = statSync(HOME_CONSOLE_DB_PATH);
			currentDbStat = { mtimeMs: stat.mtimeMs, size: stat.size };
		} catch {
			currentDbStat = null;
		}

		if (state.homeDbStat === null) {
			if (currentDbStat !== null) {
				throw new Error(
					`factory-console e2e teardown: ${HOME_CONSOLE_DB_PATH} was created during ` +
						`this run (it did not exist beforehand) — the harness leaked a write into the ` +
						`developer's real console store.`
				);
			}
		} else if (
			currentDbStat === null ||
			currentDbStat.mtimeMs !== state.homeDbStat.mtimeMs ||
			currentDbStat.size !== state.homeDbStat.size
		) {
			throw new Error(
				`factory-console e2e teardown: ${HOME_CONSOLE_DB_PATH} changed during this run ` +
					`(${JSON.stringify(state.homeDbStat)} -> ${JSON.stringify(currentDbStat)}) — the ` +
					`harness wrote into the developer's real console store.`
			);
		}
	} finally {
		rmSync(dbDir, { recursive: true, force: true });
	}
}

export default globalTeardown;
