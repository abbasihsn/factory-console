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
//
// Like the shared console, a dedicated one gets its OWN temp FACTORY_CONSOLE_DB_PATH
// (never the developer's real ~/.factory-console/console.db), removed in `dispose`
// alongside its fixture copy.
//
// `registerProject` adds a SECOND project to an already-running dedicated console
// through the live `POST /api/v1/projects` HTTP endpoint, authorized with the
// console's own write token — never through a second CLI subcommand. `cli.py` is a
// single-command Typer app (`factory-console [PATH] --no-browser --port 0`, T119's
// byte-for-byte contract), and giving the harness its own `register` subcommand would
// change how that one command must be invoked — a compatibility constraint on the
// shipped CLI that a TEST HARNESS has no business forcing onto it. Going through the
// API instead exercises the same write path a real operator (or the SPA) uses, so the
// harness never verifies a shortcut nobody else takes.

// The console prints exactly ONE line to stdout at boot:
//   "Factory Console v{version} — serving {root} at http://127.0.0.1:{port}"
// With `--port 0` the port is an OS-assigned ephemeral one (so the dedicated
// console never clashes with the shared one), read back out of this line.
const URL_PATTERN = /http:\/\/127\.0\.0\.1:\d+/;

// Grace period for the console to discover the copied project, parse its
// manifest, bind a port, and print the URL line before we give up on a cold
// boot. Matches global-setup's BOOT_TIMEOUT_MS.
const BOOT_TIMEOUT_MS = 30_000;

// The console announces this session's write token on STDERR (never stdout,
// never the logging handlers — it is a secret), as one line:
//   "X-Factory-Write-Token: {token}"
// The header name MIRRORS `factory_console.config.WRITE_TOKEN_HEADER` — source
// of truth: `server/factory_console/config.py`. Anchored to the start of a line
// so a token value that happens to contain the header text cannot be matched
// instead of the announcement itself.
const WRITE_TOKEN_PATTERN = /^X-Factory-Write-Token: (.+)$/m;

// What the console prints INSTEAD of the value when the token was pinned via
// FACTORY_CONSOLE_WRITE_TOKEN: it withholds a secret the operator already has
// rather than writing it into whatever captures stderr. We spawn with the
// ambient `process.env`, so a developer who has that variable exported would get
// this placeholder where a token is expected — worth failing on by name (see
// `awaitWriteToken`) rather than handing tests a literal `<pinned, not echoed>`
// and letting every write 401.
const PINNED_TOKEN_PLACEHOLDER = '<pinned, not echoed>';

// Poll cadence and total grace for the write-token line to turn up in the stderr
// buffer once the URL line has already arrived on stdout. Short: the console
// prints the token BEFORE the URL (see `awaitWriteToken`), so this only ever
// waits on pipe scheduling, not on the server doing more work.
const TOKEN_POLL_INTERVAL_MS = 50;
const TOKEN_TIMEOUT_MS = 5_000;

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

/** One project `registerProject` added to a dedicated console's registry. */
export interface RegisteredProject {
	readonly id: string;
	readonly name: string;
	readonly path: string;
}

/**
 * A handle to a running dedicated console over a disposable fixture copy.
 * The owning test mutates the copy through `moveRunState` (it is the sole
 * writer) and MUST call `dispose` in `afterAll` to reap the child and every
 * temp dir it (or `registerProject`) created.
 */
export interface DedicatedConsole {
	/** Base URL of the dedicated console, e.g. `http://127.0.0.1:54321`. */
	readonly baseURL: string;
	/** The temp dir holding this run's private fixture copy. */
	readonly tempDir: string;
	/**
	 * This session's write token, as announced on the console's stderr. Every
	 * mutating API call must carry it in the `X-Factory-Write-Token` header —
	 * a browser test authorizes itself by seeding it into `sessionStorage` under
	 * the SPA's own key, exactly as a pasted token would land there.
	 */
	readonly writeToken: string;
	/** Projects `registerProject` has added to this console's registry, in order. */
	readonly projects: RegisteredProject[];
	/**
	 * Move a run-state marker on the copy from one status dir to another by
	 * renaming `<tempDir>/.factory/run-state/<from>/<id>` → `.../<to>/<id>`.
	 * The destination status dir already exists in the fixture. This is the
	 * watcher-visible mutation a live test asserts a refresh for.
	 */
	moveRunState(id: string, from: string, to: string): void;
	/**
	 * SIGTERM→poll→SIGKILL the child, then remove every temp dir this handle
	 * owns (its fixture copy, its private DB dir, and any `registerProject`
	 * added). Idempotent.
	 */
	dispose(): Promise<void>;
}

// Tracks the temp dirs a handle owns beyond its own `tempDir`/db dir (its DB dir
// and any fixture copies `registerProject` creates), so `dispose` reaps all of
// them without widening the public interface with an internal bookkeeping field.
const _ownedTempDirs = new WeakMap<DedicatedConsole, string[]>();

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
 * Read this session's write token out of the accumulated stderr, waiting briefly
 * for the line if it has not landed yet.
 *
 * `cli.py` builds the app — which mints the token and announces it — BEFORE it
 * echoes the URL line, so by the time the URL has been seen the token has already
 * been written. It is written to a DIFFERENT pipe, though, and Node makes no
 * promise about how two pipes' `data` events interleave, so the buffer is polled
 * to a short deadline rather than read once and trusted.
 *
 * Throws (via `describe`, like every other boot failure) when the line never
 * appears or carries the pinned-token placeholder instead of a value — a handle
 * with no usable token would otherwise fail much later, as an unexplained 401 in
 * whatever test tried to write.
 */
async function awaitWriteToken(
	readStderr: () => string,
	describe: (reason: string) => string
): Promise<string> {
	const deadline = Date.now() + TOKEN_TIMEOUT_MS;
	for (;;) {
		const match = readStderr().match(WRITE_TOKEN_PATTERN);
		if (match) {
			const token = match[1].trim();
			if (token === PINNED_TOKEN_PLACEHOLDER) {
				throw new Error(
					describe(
						'the write token is pinned via FACTORY_CONSOLE_WRITE_TOKEN, so the console withheld ' +
							'its value — unset that variable so each dedicated console mints and announces its own'
					)
				);
			}
			return token;
		}
		if (Date.now() >= deadline) {
			throw new Error(describe('timed out waiting for the write-token line on stderr'));
		}
		await sleep(TOKEN_POLL_INTERVAL_MS);
	}
}

/**
 * Boot a dedicated console against a fresh temp copy of `fixtureName` (default
 * `with_run_state`) and resolve once it has printed its base URL AND its write
 * token. On timeout, early exit, or spawn error, the child is killed and the
 * temp dir removed before rejecting with a descriptive message — a setup failure
 * never leaks a process or a temp dir.
 */
export async function start(fixtureName: string = DEFAULT_FIXTURE): Promise<DedicatedConsole> {
	const src = path.join(REPO_ROOT, 'tests', 'fixtures', 'projects', fixtureName);

	// Copy the fixture into a private temp dir BEFORE spawning so the console
	// only ever sees the copy. `recursive` carries both file- and directory-form
	// run-state markers (CAD-140 is a bare directory with no `state` file).
	const tempDir = mkdtempSync(path.join(tmpdir(), 'factory-console-e2e-'));
	cpSync(src, tempDir, { recursive: true });

	// A private temp dir for this console's OWN SQLite store, exactly like
	// global-setup's shared boot — never the developer's real
	// ~/.factory-console/console.db. Kept separate from `tempDir` (the fixture
	// copy the console SERVES) so the two lifecycles don't collide on disk.
	const dbDir = mkdtempSync(path.join(tmpdir(), 'factory-console-e2e-db-'));

	// CRITICAL: cwd must be REPO_ROOT so a relative `PYTHONPATH=server` in the
	// env resolves to `<repo>/server` during from-source verification — exactly
	// as global-setup relies on it. The fixture PATH arg is absolute (the temp
	// dir), so discovery stays cwd-independent.
	const { bin, args } = resolveLaunch(tempDir);
	const child = spawn(bin, args, {
		cwd: REPO_ROOT,
		env: { ...process.env, FACTORY_CONSOLE_DB_PATH: path.join(dbDir, 'console.db') },
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
	let writeToken: string;
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
		// Only once the console is up: the stderr listener above stays attached, so
		// this reads the SAME accumulating buffer, waiting out any pipe-ordering lag.
		writeToken = await awaitWriteToken(() => stderr, describe);
	} catch (err) {
		// Setup failed: never leak the child (the timeout path leaves it running)
		// or either temp dir. Both cleanups swallow their own errors.
		await killChild(child).catch(() => {});
		rmSync(tempDir, { recursive: true, force: true });
		rmSync(dbDir, { recursive: true, force: true });
		throw err;
	}

	const moveRunState = (id: string, from: string, to: string): void => {
		const runState = path.join(tempDir, '.factory', 'run-state');
		renameSync(path.join(runState, from, id), path.join(runState, to, id));
	};

	const projects: RegisteredProject[] = [];

	// Robust to a partial/failed start (temp dir without a live child, or vice
	// versa) and to being called more than once: always attempts the child kill
	// and EVERY owned temp dir's removal (this console's own two, plus any
	// `registerProject` added), swallowing ESRCH and missing-path errors.
	const dispose = async (): Promise<void> => {
		try {
			await killChild(child);
		} finally {
			rmSync(tempDir, { recursive: true, force: true });
			rmSync(dbDir, { recursive: true, force: true });
			for (const extra of _ownedTempDirs.get(handle) ?? []) {
				rmSync(extra, { recursive: true, force: true });
			}
		}
	};

	const handle: DedicatedConsole = {
		baseURL,
		tempDir,
		writeToken,
		projects,
		moveRunState,
		dispose
	};
	_ownedTempDirs.set(handle, []);
	return handle;
}

// Grace period for the console to accept connections, exactly like BOOT_TIMEOUT_MS
// covers printing the URL line: the contract line is printed BEFORE server.run()
// binds the socket (see global-setup's own comment), so the very first request can
// race the bind and see ECONNREFUSED. Retried rather than delayed by a fixed sleep,
// same reasoning as global-setup's `_get_health` twin in `test_cli.py`.
const CONNECT_RETRY_TIMEOUT_MS = 5_000;
const CONNECT_RETRY_INTERVAL_MS = 50;

async function fetchWithConnectRetry(url: string, init: RequestInit): Promise<Response> {
	const deadline = Date.now() + CONNECT_RETRY_TIMEOUT_MS;
	for (;;) {
		try {
			return await fetch(url, init);
		} catch (err) {
			if (Date.now() >= deadline) throw err;
			await sleep(CONNECT_RETRY_INTERVAL_MS);
		}
	}
}

/**
 * Register a SECOND project on an already-running `handle`: copy `fixtureName`
 * into its own private temp dir (so it never shares the fixture copy `start`
 * made) and `POST` it to `/api/v1/projects`, authorized with `handle`'s write
 * token. Appends the new row to `handle.projects` and returns it.
 *
 * The new temp dir is owned by `handle` from this call onward — `dispose` reaps
 * it exactly as it reaps `handle.tempDir`, so a multi-project spec still leaks
 * nothing.
 */
export async function registerProject(
	handle: DedicatedConsole,
	fixtureName: string
): Promise<RegisteredProject> {
	const src = path.join(REPO_ROOT, 'tests', 'fixtures', 'projects', fixtureName);
	const tempDir = mkdtempSync(path.join(tmpdir(), 'factory-console-e2e-'));
	cpSync(src, tempDir, { recursive: true });
	_ownedTempDirs.get(handle)?.push(tempDir);

	const response = await fetchWithConnectRetry(`${handle.baseURL}/api/v1/projects`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			'X-Factory-Write-Token': handle.writeToken
		},
		body: JSON.stringify({ path: tempDir })
	});
	if (!response.ok) {
		throw new Error(
			`registerProject(${fixtureName}): POST /api/v1/projects → ${response.status}: ` +
				(await response.text())
		);
	}

	const row = (await response.json()) as { id: string; name: string; path: string };
	const project: RegisteredProject = { id: row.id, name: row.name, path: row.path };
	handle.projects.push(project);
	return project;
}

/**
 * Boot a dedicated console on `fixtures[0]` (via `start`), then `registerProject`
 * every remaining entry against it in order. Requires at least one fixture — the
 * console has to boot on something.
 *
 * `start`'s own invariant — a setup failure never leaks a process or a temp dir —
 * holds only up to the point `start` resolves. A `registerProject` failure after
 * that (a 409 duplicate path, a 503, a connect-retry deadline) must not propagate
 * past a live, undisposed `handle` that the caller never receives and so can
 * never dispose of either.
 */
export async function startMulti(fixtures: string[]): Promise<DedicatedConsole> {
	if (fixtures.length === 0) {
		throw new Error('startMulti: at least one fixture is required to boot on');
	}
	const [first, ...rest] = fixtures;
	const handle = await start(first);
	try {
		for (const fixtureName of rest) {
			await registerProject(handle, fixtureName);
		}
	} catch (err) {
		await handle.dispose().catch(() => {});
		throw err;
	}
	return handle;
}
