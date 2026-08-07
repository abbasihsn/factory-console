import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Where global-teardown reads the console child's PID to shut it down. A stable
// name in the OS temp dir so teardown finds it in a fresh process.
export const PID_FILE = path.join(tmpdir(), 'factory-console-e2e.pid');

// Where global-teardown reads this run's temp DB dir and pre-run snapshot of
// ~/.factory-console/ (see DB_STATE_FILE's writer below) to verify the run never
// touched the developer's real console store.
export const DB_STATE_FILE = path.join(tmpdir(), 'factory-console-e2e.db-state.json');

// The developer's (and CI runner's) real console store — the ONE thing no layer
// of this harness may ever create or modify. Named here, not re-derived in
// global-teardown, so there is one spelling of "the real store" for both ends of
// the guard.
export const HOME_FACTORY_CONSOLE_DIR = path.join(homedir(), '.factory-console');

// The artifact the guard actually compares — NOT the directory. A directory's
// mtime only changes when an entry is added or removed, not when a file already
// inside it (this one) is written, so comparing `HOME_FACTORY_CONSOLE_DIR` itself
// misses a write to an already-existing store (a false negative) and trips on
// any unrelated tool that merely touches the directory (a false positive, e.g.
// the developer's own console running concurrently).
export const HOME_CONSOLE_DB_PATH = path.join(HOME_FACTORY_CONSOLE_DIR, 'console.db');

// The console prints exactly ONE line to stdout at boot:
//   "Factory Console v{version} — serving {root} at http://127.0.0.1:{port}"
// With the default `--port 0` that port is an OS-assigned ephemeral one, so we
// read the whole base URL back out of this line.
const URL_PATTERN = /http:\/\/127\.0\.0\.1:\d+/;

// Grace period for the console to discover the project, parse the manifest, bind
// a port, and print the URL line before we give up on a cold boot.
const BOOT_TIMEOUT_MS = 30_000;

// The frontend package is `"type": "module"`, so `__dirname` isn't defined —
// derive paths from the module URL instead. frontend/tests/e2e -> repo root is
// three levels up.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, '../../..');
const FIXTURE_ROOT = path.join(REPO_ROOT, 'tests', 'fixtures', 'projects', 'with_run_state');

// The console binary is `factory-console` (installed from the wheel in CI — the
// default, used verbatim there). FC_E2E_CONSOLE_CMD overrides the launcher so the
// harness stays verifiable locally/headless WITHOUT the installed wheel — e.g.
// `python3.13 -m factory_console` running the in-repo `server/` package. The value
// is split on whitespace (naive, no shell quoting — fine for our controlled
// values): first token is the executable, the rest are leading args.
function resolveLaunch(): { bin: string; args: string[] } {
	const cmd = (process.env.FC_E2E_CONSOLE_CMD ?? 'factory-console').trim();
	const [bin, ...leadingArgs] = cmd.split(/\s+/);
	// Absolute fixture path makes discovery cwd-independent; repo-root cwd lets a
	// relative PYTHONPATH=server resolve during from-source verification.
	return { bin, args: [...leadingArgs, FIXTURE_ROOT, '--no-browser', '--port', '0'] };
}

async function globalSetup(): Promise<void> {
	// A per-run temp dir for the console's OWN SQLite store, passed to the spawned
	// child via FACTORY_CONSOLE_DB_PATH so this boot — and every fixture/spec that
	// registers a second project against it — never opens the developer's real
	// ~/.factory-console/console.db.
	const dbDir = mkdtempSync(path.join(tmpdir(), 'factory-console-e2e-db-'));
	const dbPath = path.join(dbDir, 'console.db');

	// Snapshot the real store's own db file — existence, mtime AND size — BEFORE
	// the child ever runs (null if it does not exist yet), so global-teardown can
	// prove this run left it untouched.
	let homeDbStat: { mtimeMs: number; size: number } | null;
	try {
		const stat = statSync(HOME_CONSOLE_DB_PATH);
		homeDbStat = { mtimeMs: stat.mtimeMs, size: stat.size };
	} catch {
		homeDbStat = null;
	}

	const { bin, args } = resolveLaunch();
	const child = spawn(bin, args, {
		cwd: REPO_ROOT,
		env: { ...process.env, FACTORY_CONSOLE_DB_PATH: dbPath },
		stdio: ['ignore', 'pipe', 'pipe']
	});

	let stdout = '';
	let stderr = '';
	const describe = (reason: string): string =>
		[
			`factory-console e2e setup: ${reason}.`,
			`launch: ${bin} ${args.join(' ')}`,
			`cwd: ${REPO_ROOT}`,
			`--- stdout ---\n${stdout || '(empty)'}`,
			`--- stderr ---\n${stderr || '(empty)'}`
		].join('\n');

	let baseURL: string;
	try {
		baseURL = await new Promise<string>((resolve, reject) => {
			let settled = false;
			const finish = (action: () => void): void => {
				if (settled) return;
				settled = true;
				clearTimeout(timer);
				action();
			};
			const timer = setTimeout(
				() => finish(() => reject(new Error(describe('timed out waiting for the URL line')))),
				BOOT_TIMEOUT_MS
			);

			// ENOENT (binary not on PATH) and similar spawn failures arrive here.
			child.on('error', (err) =>
				finish(() => reject(new Error(describe(`failed to spawn (${err.message})`))))
			);
			// The console exiting before it prints a URL is always a setup failure.
			child.on('exit', (code, signal) =>
				finish(() =>
					reject(new Error(describe(`console exited early (code=${code}, signal=${signal})`)))
				)
			);
			// Accumulate stdout and resolve on the first URL match. The listeners stay
			// attached afterward so both pipes keep draining and never block the child.
			child.stdout!.on('data', (chunk: Buffer) => {
				stdout += chunk.toString();
				const match = stdout.match(URL_PATTERN);
				if (match) finish(() => resolve(match[0]));
			});
			child.stderr!.on('data', (chunk: Buffer) => {
				stderr += chunk.toString();
			});
		});
	} catch (err) {
		// globalTeardown does NOT run when globalSetup throws, so don't leak a child
		// that's still alive (the timeout path leaves it running) OR the temp DB dir.
		if (child.pid !== undefined && child.exitCode === null && child.signalCode === null) {
			child.kill('SIGKILL');
		}
		rmSync(dbDir, { recursive: true, force: true });
		throw err;
	}

	writeFileSync(PID_FILE, String(child.pid), 'utf8');
	writeFileSync(DB_STATE_FILE, JSON.stringify({ dbDir, homeDbStat }), 'utf8');
	// Workers are spawned only after globalSetup resolves, so this env var is in
	// place when each worker re-loads playwright.config.ts and reads use.baseURL.
	process.env.FC_E2E_BASE_URL = baseURL;
}

export default globalSetup;
