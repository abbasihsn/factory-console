import { readFileSync, rmSync } from 'node:fs';
import { PID_FILE } from './global-setup';

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
}

export default globalTeardown;
