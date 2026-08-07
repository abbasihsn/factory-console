import { readFileSync, rmSync, statSync } from 'node:fs';
import { DB_STATE_FILE, HOME_FACTORY_CONSOLE_DIR, PID_FILE } from './global-setup';

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

	await assertRealStoreUntouchedAndCleanUp();
}

// The protection this whole harness exists to prove: with the child now dead,
// confirm ~/.factory-console/ was not created (or, if it already existed, that
// its mtime is unchanged) before removing this run's own temp DB dir. Throwing
// here fails the teardown itself — a leak into the developer's real store is a
// harness regression, not a thing to clean up quietly and move on from.
async function assertRealStoreUntouchedAndCleanUp(): Promise<void> {
	let state: { dbDir: string; homeDirMtimeMs: number | null };
	try {
		state = JSON.parse(readFileSync(DB_STATE_FILE, 'utf8'));
	} catch {
		// No state file — global-setup never got far enough to write one (it threw
		// before doing so), so there is nothing recorded to check or clean up.
		return;
	}

	let currentMtimeMs: number | null;
	try {
		currentMtimeMs = statSync(HOME_FACTORY_CONSOLE_DIR).mtimeMs;
	} catch {
		currentMtimeMs = null;
	}

	if (state.homeDirMtimeMs === null) {
		if (currentMtimeMs !== null) {
			throw new Error(
				`factory-console e2e teardown: ${HOME_FACTORY_CONSOLE_DIR} was created during ` +
					`this run (it did not exist beforehand) — the harness leaked a write into the ` +
					`developer's real console store.`
			);
		}
	} else if (currentMtimeMs !== state.homeDirMtimeMs) {
		throw new Error(
			`factory-console e2e teardown: ${HOME_FACTORY_CONSOLE_DIR}'s mtime changed during ` +
				`this run (${state.homeDirMtimeMs} -> ${currentMtimeMs}) — the harness wrote into ` +
				`the developer's real console store.`
		);
	}

	rmSync(state.dbDir, { recursive: true, force: true });
	rmSync(DB_STATE_FILE, { force: true });
}

export default globalTeardown;
