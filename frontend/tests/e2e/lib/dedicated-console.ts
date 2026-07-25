import { spawn, type ChildProcess } from 'node:child_process';
import { cpSync, mkdtempSync, renameSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Reusable e2e harness helper that boots a DEDICATED, second factory-console
// against an isolated temp COPY of a read-only fixture. The shared fixtures and
// the shared (single-worker) console booted by global-setup are contractually
// read-only, so any test that needs to MUTATE the project on disk — e.g. to
// prove a watcher-detected change refreshes the open view over SSE — copies the
// fixture here, mutates the copy (it is the sole writer), and disposes the
// dedicated console + temp dir afterward. This file lives under `lib/` so it is
// NOT matched by Playwright's `*.spec` test glob and never runs as a test.
//
// It deliberately re-derives the launcher, URL pattern, and process-hygiene
// mechanics from global-setup / global-teardown rather than importing them:
// global-setup owns the SHARED console's lifecycle (a PID file, a single
// `--port 0` server, an exported base-URL env var), and coupling to it would
// entangle the two lifecycles. The mechanisms below mirror those files exactly.

// The console prints exactly ONE line to stdout at boot:
//   "Factory Console v{version} — serving {root} at http://127.0.0.1:{port}"
// With `--port 0` the port is an OS-assigned ephemeral one (so the dedicated
// console never clashes with the shared one), read back out of this line.
const URL_PATTERN = /http:\/\/127\.0\.0\.1:\d+/;

// Grace period for the console to discover the copied project, parse its
// manifest, bind a port, and print the URL line before we give up on a cold
// boot. Matches global-setup's BOOT_TIMEOUT_MS.
const BOOT_TIMEOUT_MS = 30_000;

// Poll cadence and total grace given to a clean SIGTERM shutdown (uvicorn drains
// and exits 0) before escalating to SIGKILL. Matches global-teardown.
const KILL_POLL_INTERVAL_MS = 100;
const KILL_TIMEOUT_MS = 5_000;

// The default fixture: the run-state project used across the e2e suite. Its
// run-state markers come in BOTH forms — file-form (`<id>/state`) and bare
// directory-form (`.factory/run-state/todo/CAD-140`, no `state` file inside) —
// and `cpSync({ recursive: true })` carries both faithfully.
const DEFAULT_FIXTURE = 'with_run_state';

// The frontend package is `"type": "module"`, so `__dirname` isn't defined —
// derive paths from the module URL instead, exactly like global-setup does.
// frontend/tests/e2e/lib -> repo root is FOUR levels up.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, '../../../..');

/**
 * A handle to a running dedicated console over a disposable fixture copy.
 * The owning test mutates the copy through `moveRunState` (it is the sole
 * writer) and MUST call `dispose` in `afterAll` to reap the child and temp dir.
 */
export interface DedicatedConsole {
	/** Base URL of the dedicated console, e.g. `http://127.0.0.1:54321`. */
	readonly baseURL: string;
	/** The temp dir holding this run's private fixture copy. */
	readonly tempDir: string;
	/**
	 * Move a run-state marker on the copy from one status dir to another by
	 * renaming `<tempDir>/.factory/run-state/<from>/<id>` → `.../<to>/<id>`.
	 * The destination status dir already exists in the fixture. This is the
	 * watcher-visible mutation a live test asserts a refresh for.
	 */
	moveRunState(id: string, from: string, to: string): void;
	/** SIGTERM→poll→SIGKILL the child, then remove the temp dir. Idempotent. */
	dispose(): Promise<void>;
}

// The console binary is `factory-console` (installed from the wheel in CI — the
// default). FC_E2E_CONSOLE_CMD overrides the launcher so the harness stays
// verifiable locally/headless WITHOUT the installed wheel — e.g.
// `python3.13 -m factory_console` running the in-repo `server/` package. Split
// on whitespace (naive, no shell quoting — fine for our controlled values):
// first token is the executable, the rest are leading args. Resolved
// INDEPENDENTLY of global-setup so the two consoles never share config.
function resolveLaunch(tempDir: string): { bin: string; args: string[] } {
	const cmd = (process.env.FC_E2E_CONSOLE_CMD ?? 'factory-console').trim();
	const [bin, ...leadingArgs] = cmd.split(/\s+/);
	return { bin, args: [...leadingArgs, tempDir, '--no-browser', '--port', '0'] };
}

function sleep(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

// A child is alive while neither its exit code nor its terminating signal has
// been recorded — Node sets exactly one of the two once the process exits.
function isChildAlive(child: ChildProcess): boolean {
	return child.exitCode === null && child.signalCode === null;
}

// SIGTERM the child, poll for a graceful exit, and force-kill if it overstays so
// no orphaned console lingers. Swallows ESRCH (already gone). Mirrors
// global-teardown's shutdown, but polls the ChildProcess directly (we hold the
// handle here) instead of a PID file.
async function killChild(child: ChildProcess): Promise<void> {
	if (!isChildAlive(child)) return;
	try {
		child.kill('SIGTERM');
	} catch (err) {
		if ((err as NodeJS.ErrnoException).code !== 'ESRCH') throw err;
		return;
	}

	const deadline = Date.now() + KILL_TIMEOUT_MS;
	while (isChildAlive(child) && Date.now() < deadline) {
		await sleep(KILL_POLL_INTERVAL_MS);
	}
	if (isChildAlive(child)) {
		try {
			child.kill('SIGKILL');
		} catch (err) {
			if ((err as NodeJS.ErrnoException).code !== 'ESRCH') throw err;
		}
	}
}

/**
 * Boot a dedicated console against a fresh temp copy of `fixtureName` (default
 * `with_run_state`) and resolve once it has printed its base URL. On timeout,
 * early exit, or spawn error, the child is killed and the temp dir removed
 * before rejecting with a descriptive message — a setup failure never leaks a
 * process or a temp dir.
 */
export async function start(fixtureName: string = DEFAULT_FIXTURE): Promise<DedicatedConsole> {
	const src = path.join(REPO_ROOT, 'tests', 'fixtures', 'projects', fixtureName);

	// Copy the fixture into a private temp dir BEFORE spawning so the console
	// only ever sees the copy. `recursive` carries both file- and directory-form
	// run-state markers (CAD-140 is a bare directory with no `state` file).
	const tempDir = mkdtempSync(path.join(tmpdir(), 'factory-console-e2e-'));
	cpSync(src, tempDir, { recursive: true });

	// CRITICAL: cwd must be REPO_ROOT so a relative `PYTHONPATH=server` in the
	// env resolves to `<repo>/server` during from-source verification — exactly
	// as global-setup relies on it. The fixture PATH arg is absolute (the temp
	// dir), so discovery stays cwd-independent.
	const { bin, args } = resolveLaunch(tempDir);
	const child = spawn(bin, args, {
		cwd: REPO_ROOT,
		env: process.env,
		stdio: ['ignore', 'pipe', 'pipe']
	});

	let stdout = '';
	let stderr = '';
	const describe = (reason: string): string =>
		[
			`dedicated factory-console: ${reason}.`,
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
		// Setup failed: never leak the child (the timeout path leaves it running)
		// or the temp dir. Both cleanups swallow their own errors.
		await killChild(child).catch(() => {});
		rmSync(tempDir, { recursive: true, force: true });
		throw err;
	}

	const moveRunState = (id: string, from: string, to: string): void => {
		const runState = path.join(tempDir, '.factory', 'run-state');
		renameSync(path.join(runState, from, id), path.join(runState, to, id));
	};

	// Robust to a partial/failed start (temp dir without a live child, or vice
	// versa) and to being called more than once: always attempts BOTH the child
	// kill and the temp-dir removal, swallowing ESRCH and missing-path errors.
	const dispose = async (): Promise<void> => {
		try {
			await killChild(child);
		} finally {
			rmSync(tempDir, { recursive: true, force: true });
		}
	};

	return { baseURL, tempDir, moveRunState, dispose };
}
