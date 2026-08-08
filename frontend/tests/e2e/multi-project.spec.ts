import { test, expect, type Page } from '@playwright/test';
import { mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
	copyFixture,
	fetchWithConnectRetry,
	registerProjectDir,
	start,
	startMulti,
	type DedicatedConsole
} from './lib/dedicated-console';

// v3.0's headline claim — "the console shows one project at a time and you can
// switch which one" — driven through a real browser against a real TWO-project
// console. Everything the frontend track decided is proven here rather than
// asserted: where a switch lands, what a deep link into a ticket the new project
// has never heard of renders, that a degraded project SAYS so instead of showing
// an empty table, that the live pill never flashes Offline across a switch, and
// that the boot-time PATH is still the session's initial selection (T111).
//
// This spec is the SOLE WRITER of its own registry. The shared console booted by
// global-setup stays single-project and read-only — every registry mutation below
// (register, remove, switch) would otherwise be visible to every other spec — so
// it boots a DEDICATED console over private temp copies (see
// ./lib/dedicated-console) and navigates by that console's OWN absolute base URL,
// NOT `use.baseURL`. Nothing here ever touches ~/.factory-console/.
//
// No `networkidle` anywhere: the SSE stream never lets the network idle, so every
// wait is a web-first, role/label-based assertion.

// MIRRORS `STORAGE_KEY` in `frontend/src/lib/stores/writeToken.ts` — the key that
// module hydrates from at import. Keep in sync with it.
const WRITE_TOKEN_STORAGE_KEY = 'factory-console:writeToken';

// MIRRORS `SESSION_PROJECT_ID` in
// `server/factory_console/services/project_selection.py`: the reserved id of the
// ephemeral, unregistered project a `factory-console PATH` boot pins. It is the
// `<option value>` of the switcher's first row.
const SESSION_PROJECT_ID = 'session';

// One ticket id from each fixture, standing in for "this project's manifest".
// They are disjoint on purpose: `with_run_state` is the `CAD-*` habit tracker,
// `minimal` the `TM-*` trail-report project, and an id from one is simply absent
// from the other — which is exactly the deep-link case below.
const RUN_STATE_TICKET = 'CAD-140';
const MINIMAL_TICKET = 'TM-015';
const MINIMAL_GRAPH_ROOT = 'TM-001';

// The pinned project (`with_run_state`) carries a populated `.factory/`, so its
// condition is `ok`; `minimal` deliberately has none, so its condition is
// `no_factory_dir` — the two fixtures differ in exactly the conditions this
// milestone has to name. The switcher renders every non-`ok` row DISABLED, so a
// switch driven through the dropdown can only ever land ON the `ok` project:
// every UI-switch test below therefore ARRANGES the degraded project as the
// selection (through the API, as a prior session would have left it) and ACTS by
// switching to the pinned one.
const DEGRADED_CONDITION = 'no_factory_dir';
// MIRRORS `CONDITION_TITLE.no_factory_dir` in `$lib/projects/conditionTitle`.
const DEGRADED_CONDITION_TITLE = 'This project has no .factory/ directory on this machine.';

// Assigned in beforeAll; read by the tests and afterAll. Left undefined if
// beforeAll's `startMulti()` throws, so afterAll guards before disposing.
let dedicated: DedicatedConsole | undefined;

// Temp dirs this SPEC created and must remove itself — the ones that outlive any
// single console handle (a project registered through the browser form, and the
// precedence test's shared registry). Everything a handle created is reaped by
// its own `dispose`.
const specOwnedDirs: string[] = [];

function ownedCopy(fixtureName: string): string {
	const dir = copyFixture(fixtureName);
	specOwnedDirs.push(dir);
	return dir;
}

/** The name the console gives a registered temp copy: its directory's basename. */
function projectName(dir: string): string {
	return path.basename(dir);
}

// Every direct API call goes through the connect-retrying fetch: the console
// prints its URL BEFORE uvicorn binds the socket, so a handle's very first
// request can race the bind — which the precedence test below hits by design,
// since it reads a console it has only just booted.
async function readJson<T>(url: string): Promise<T> {
	const response = await fetchWithConnectRetry(url);
	if (!response.ok) {
		throw new Error(`GET ${url} → ${response.status}: ${await response.text()}`);
	}
	return (await response.json()) as T;
}

/**
 * Point the console at `projectId` through the same `PUT /api/v1/projects/current`
 * the SPA's switcher calls.
 *
 * Used to ARRANGE a starting selection (including onto the degraded project the
 * dropdown refuses to offer), never to stand in for the act a test is about — the
 * switches under test are all driven through the `<select>`.
 */
async function selectProject(handle: DedicatedConsole, projectId: string): Promise<void> {
	const response = await fetchWithConnectRetry(`${handle.baseURL}/api/v1/projects/current`, {
		method: 'PUT',
		headers: {
			'Content-Type': 'application/json',
			'X-Factory-Write-Token': handle.writeToken
		},
		body: JSON.stringify({ projectId })
	});
	if (!response.ok) {
		throw new Error(
			`PUT /api/v1/projects/current ${projectId} → ${response.status}: ${await response.text()}`
		);
	}
}

/** The id the console reports as selected right now, or `null` with a reason. */
async function currentSelectionId(handle: DedicatedConsole): Promise<string | null> {
	const body = await readJson<{ selected: { id: string } | null }>(
		`${handle.baseURL}/api/v1/projects/current`
	);
	return body.selected?.id ?? null;
}

/** The root `GET /api/v1/project` resolves for the CURRENT selection. */
async function servedRoot(handle: DedicatedConsole): Promise<string> {
	const body = await readJson<{ rootPath: string }>(`${handle.baseURL}/api/v1/project`);
	return body.rootPath;
}

/** The registry rows, as the switcher and `/projects` read them. */
async function listProjects(
	handle: DedicatedConsole
): Promise<Array<{ id: string; name: string; registered: boolean }>> {
	const body = await readJson<{ items: Array<{ id: string; name: string; registered: boolean }> }>(
		`${handle.baseURL}/api/v1/projects`
	);
	return body.items;
}

/** The header switcher. `exact` so it can't match `AddProjectForm`'s "Project path". */
function switcher(page: Page) {
	return page.getByRole('combobox', { name: 'Project', exact: true });
}

/**
 * Authorize the session the way a pasted token would, before any page script
 * runs — so the store hydrates from it on the SPA's very first load.
 *
 * Every switch below needs it: `PUT /projects/current` is write-token gated (it
 * changes what every read endpoint returns for every client), so an unauthorized
 * switcher parks the switch behind its prompt instead of performing it.
 */
async function seedWriteToken(page: Page, handle: DedicatedConsole): Promise<void> {
	await page.addInitScript(
		([key, token]) => sessionStorage.setItem(key, token),
		[WRITE_TOKEN_STORAGE_KEY, handle.writeToken]
	);
}

test.beforeAll(async () => {
	// `with_run_state` boots as the PINNED (session) project; `minimal` is
	// registered against the same console over the API.
	dedicated = await startMulti(['with_run_state', 'minimal']);
});

test.afterAll(async () => {
	// Guard: dispose even if beforeAll partially failed (startMulti cleans up its
	// own leaks on throw, so `dedicated` is only set on a fully-booted handle).
	await dedicated?.dispose();
	for (const dir of specOwnedDirs) {
		rmSync(dir, { recursive: true, force: true });
	}
});

test('multi-project: the switcher lists every project the console tracks, by name', async ({
	page
}) => {
	const handle = dedicated!;
	const minimal = handle.projects[0];

	await page.goto(handle.baseURL + '/');
	await expect(page.getByRole('heading', { name: 'Tickets', level: 1 })).toBeVisible();

	// The whole dropdown, in order: the pinned session row first, then the
	// registry rows, then the management route. The degraded row is LISTED — the
	// listing never drops one — and says so in its own label.
	await expect(switcher(page).locator('option')).toHaveText([
		projectName(handle.tempDir),
		`${minimal.name} (unavailable)`,
		'Manage projects…'
	]);
});

test('multi-project: switching from / re-renders the list with the other project’s tickets', async ({
	page
}) => {
	const handle = dedicated!;
	const minimal = handle.projects[0];

	await seedWriteToken(page, handle);
	await selectProject(handle, minimal.id);
	await page.goto(handle.baseURL + '/');

	await test.step('the list shows the selected project’s manifest', async () => {
		await expect(page.getByRole('link', { name: MINIMAL_TICKET, exact: true })).toBeVisible();
		await expect(page.getByRole('link', { name: RUN_STATE_TICKET, exact: true })).toHaveCount(0);
	});

	await test.step('switching re-renders the list with the other manifest', async () => {
		await switcher(page).selectOption(SESSION_PROJECT_ID);
		await expect(page.getByRole('link', { name: RUN_STATE_TICKET, exact: true })).toBeVisible();
		await expect(page.getByRole('link', { name: MINIMAL_TICKET, exact: true })).toHaveCount(0);
		// `/` names a VIEW, not a record, so the switch stays put.
		await expect(page).toHaveURL(/\/$/);
	});
});

test('multi-project: switching from /graph stays on /graph with the other project’s graph', async ({
	page
}) => {
	const handle = dedicated!;
	const minimal = handle.projects[0];

	await seedWriteToken(page, handle);
	await selectProject(handle, minimal.id);
	await page.goto(handle.baseURL + '/graph');
	await expect(page.getByRole('heading', { name: 'Dependency graph', level: 1 })).toBeVisible();

	// Cytoscape paints to an opaque canvas; DepGraph's visually-hidden companion
	// nav is the DOM surface — one link per NODE, named by ticket id.
	const nodeNav = page.getByRole('navigation', { name: 'Ticket dependency nodes' });
	await expect(nodeNav.getByRole('link', { name: MINIMAL_GRAPH_ROOT, exact: true })).toBeVisible();

	await switcher(page).selectOption(SESSION_PROJECT_ID);

	// `/graph` describes the new project as well as it described the old one, so
	// the route survives the switch — only its contents change.
	await expect(page).toHaveURL(/\/graph$/);
	await expect(nodeNav.getByRole('link', { name: RUN_STATE_TICKET, exact: true })).toBeVisible();
	await expect(nodeNav.getByRole('link', { name: MINIMAL_GRAPH_ROOT, exact: true })).toHaveCount(0);
});

test('multi-project: switching while on a ticket detail lands on /', async ({ page }) => {
	const handle = dedicated!;
	const minimal = handle.projects[0];

	await seedWriteToken(page, handle);
	await selectProject(handle, minimal.id);
	await page.goto(handle.baseURL + `/tickets/${MINIMAL_TICKET}`);
	// The detail page header and the rendered body both carry the title.
	await expect(
		page.getByRole('heading', { name: 'Public trail-status REST endpoint', level: 1 }).first()
	).toBeVisible();
	await expect(page).toHaveURL(new RegExp(`/tickets/${MINIMAL_TICKET}$`));

	await switcher(page).selectOption(SESSION_PROJECT_ID);

	// A ticket id is a fact about ONE project's manifest, so `switchTarget` sends
	// this route home rather than deep-linking into an id the new project has
	// never heard of.
	await expect(page).toHaveURL(/\/$/);
	await expect(page.getByRole('heading', { name: 'Tickets', level: 1 })).toBeVisible();
	await expect(page.getByRole('link', { name: RUN_STATE_TICKET, exact: true })).toBeVisible();
});

test('multi-project: a deep link to a ticket the selected project lacks names it as not found', async ({
	page
}) => {
	const handle = dedicated!;
	const minimal = handle.projects[0];

	await selectProject(handle, minimal.id);
	// Hand-typed (or bookmarked): deliberately NOT redirected — only a SWITCH is.
	// The detail loader's 404 → `notFound` panel names the case better than a
	// silent bounce to the index would.
	await page.goto(handle.baseURL + `/tickets/${RUN_STATE_TICKET}`);

	await expect(page.getByText(`Ticket "${RUN_STATE_TICKET}" not found`)).toBeVisible();
	await expect(page.getByRole('link', { name: 'back to list' })).toBeVisible();
	// Never a blank page or a boundary stack trace: the shell is still there.
	await expect(switcher(page)).toBeVisible();
});

test('multi-project: a project with no .factory/ names its condition instead of showing empty views', async ({
	page
}) => {
	const handle = dedicated!;
	const minimal = handle.projects[0];

	await selectProject(handle, minimal.id);
	await page.goto(handle.baseURL + '/');

	const banner = page.getByTestId('project-condition');

	await test.step('the shell banner names the condition', async () => {
		await expect(banner).toBeVisible();
		await expect(banner).toHaveAttribute('data-condition', DEGRADED_CONDITION);
		await expect(banner).toContainText(DEGRADED_CONDITION_TITLE);
	});

	await test.step('/runs says unknown-on-this-machine rather than rendering as measured zero', async () => {
		await page.goto(handle.baseURL + '/runs');
		// The banner lives in the shell precisely so it reaches the artefact views:
		// an absent source is a named condition, never an empty table.
		await expect(banner).toBeVisible();
		await expect(banner).toHaveAttribute('data-condition', DEGRADED_CONDITION);
	});

	await test.step('the pinned, unregistered session row raises no banner', async () => {
		await selectProject(handle, SESSION_PROJECT_ID);
		await page.goto(handle.baseURL + '/');
		await expect(page.getByRole('heading', { name: 'Tickets', level: 1 })).toBeVisible();
		await expect(banner).toHaveCount(0);
	});
});

test('multi-project: the live indicator never flashes Offline across a switch', async ({
	page
}) => {
	const handle = dedicated!;
	const minimal = handle.projects[0];

	await seedWriteToken(page, handle);
	await selectProject(handle, minimal.id);
	await page.goto(handle.baseURL + '/');

	const indicator = page.getByText('Live', { exact: true });
	await expect(indicator).toBeVisible();

	// Record every label the pill ever renders from here on. A polled read could
	// step over a transient state between samples, so this observes the DOM
	// itself — and observes `document.body` rather than the pill, so a re-render
	// that REPLACED the element could not silently detach the observer and pass.
	await page.evaluate(() => {
		const seen: string[] = [];
		(window as unknown as { __liveLabels: string[] }).__liveLabels = seen;
		const read = (): string =>
			document.querySelector('span[aria-live="polite"]')?.textContent?.trim() ?? '';
		const record = (): void => {
			const label = read();
			if (label !== '' && label !== seen[seen.length - 1]) seen.push(label);
		};
		record();
		new MutationObserver(record).observe(document.body, {
			subtree: true,
			childList: true,
			characterData: true
		});
	});

	await switcher(page).selectOption(SESSION_PROJECT_ID);
	await expect(page.getByRole('link', { name: RUN_STATE_TICKET, exact: true })).toBeVisible();
	// The stream follows the selection on a NEW connection (the server resolves a
	// stream's project once per connection), so the pill settles back on `Live`.
	await expect(indicator).toBeVisible();

	// T126 restarts the stream on a selection change specifically so the switch
	// never transits `disconnected` — "Offline" is what a user would have seen.
	const labels = await page.evaluate(
		() => (window as unknown as { __liveLabels: string[] }).__liveLabels
	);
	expect(labels).not.toContain('Offline');
	expect(labels[labels.length - 1]).toBe('Live');
	// The pill DID re-render mid-switch (the stream really was re-opened), so the
	// assertion above is about a transition that happened rather than one the
	// observer slept through: `restart()` sets `disconnected` then `connecting` in
	// one synchronous block, and it is that batching which keeps "Offline" off
	// screen. An observer that recorded nothing would pass vacuously.
	expect(labels.length).toBeGreaterThan(1);
});

test('multi-project: /projects registers a third project, names a bad path, and removes a row', async ({
	page
}) => {
	const handle = dedicated!;
	const minimal = handle.projects[0];
	const thirdDir = ownedCopy('second');
	const thirdName = projectName(thirdDir);

	await seedWriteToken(page, handle);
	await page.goto(handle.baseURL + '/projects');
	await expect(page.getByRole('heading', { name: 'Projects', level: 1 })).toBeVisible();

	await test.step('registering a path through the form adds it to the switcher', async () => {
		await page.getByLabel('Project path').fill(thirdDir);
		await page.getByRole('button', { name: 'Register', exact: true }).click();
		// No optimistic row: the page re-reads, so seeing it means the server has it.
		await expect(switcher(page).locator('option')).toHaveText([
			projectName(handle.tempDir),
			`${minimal.name} (unavailable)`,
			`${thirdName} (unavailable)`,
			'Manage projects…'
		]);
	});

	await test.step('a path that is not a factory project is refused BY NAME', async () => {
		// Absolute and well-formed, so it gets past canonicalisation and is refused
		// by DISCOVERY — the distinction the form exists to surface verbatim.
		const bogusPath = path.join(tmpdir(), 'factory-console-e2e-not-a-project-T127');
		await page.getByLabel('Project path').fill(bogusPath);
		await page.getByRole('button', { name: 'Register', exact: true }).click();
		// `ApiErrorView` prints the envelope's `code` and `message` verbatim: which
		// of the several ways a path can be wrong fired is the whole point.
		await expect(page.getByText('project_not_found', { exact: true })).toBeVisible();
		await expect(
			page.getByRole('heading', {
				name: /^No App Factory project found for .+: missing docs\/planning\/tickets\.json\.$/
			})
		).toBeVisible();
		await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
	});

	await test.step('removing a row asks first, then drops it from the switcher', async () => {
		const third = (await listProjects(handle)).find((row) => row.name === thirdName);
		expect(third, `no registry row named ${thirdName}`).toBeDefined();

		await page
			.getByTestId(`project-row-${third!.id}`)
			.getByRole('button', { name: 'Remove' })
			.click();
		const confirm = page.getByRole('dialog', { name: 'Remove project?' });
		await expect(confirm).toBeVisible();
		// The confirmation names what it forgets, and says the disk is untouched.
		await expect(confirm).toContainText(thirdName);
		await confirm.getByRole('button', { name: 'Remove project' }).click();

		await expect(page.getByTestId(`project-row-${third!.id}`)).toHaveCount(0);
		await expect(switcher(page).locator('option')).toHaveText([
			projectName(handle.tempDir),
			`${minimal.name} (unavailable)`,
			'Manage projects…'
		]);
	});
});

test('multi-project: registering with no write token asks for one instead of failing silently', async ({
	page
}) => {
	const handle = dedicated!;
	// No `seedWriteToken` here, deliberately. Clearing the key in-page would prove
	// nothing: the store hydrates ONCE at import, so removing the value without
	// reloading leaves the in-memory token intact — and `addInitScript` re-runs on
	// every reload, which would put it straight back. A context that never held
	// one is the exact state the SPA sees after a token is dropped.
	const candidateDir = ownedCopy('second');

	await page.goto(handle.baseURL + '/projects');
	await page.getByLabel('Project path').fill(candidateDir);
	await page.getByRole('button', { name: 'Register', exact: true }).click();

	// The submit is PARKED behind the prompt, not failed: the form is still there
	// with the path as typed, so pasting a token resumes this very add.
	await expect(page.getByRole('heading', { name: 'Write token required' })).toBeVisible();
	await expect(page.getByLabel('Write token')).toBeVisible();
	await expect(page.getByLabel('Project path')).toHaveValue(candidateDir);
	// And nothing was written: a silent failure would have registered it anyway or
	// left the user with no explanation at all.
	expect((await listProjects(handle)).map((row) => row.name)).not.toContain(
		projectName(candidateDir)
	);
});

test('multi-project: the pinned PATH is the session’s initial selection, and a switch still takes effect', async () => {
	// T111's precedence rule is a statement about BOOT, so one console cannot show
	// it: the process-local session selection is re-seeded to the pin at EVERY
	// start, regardless of what the registry persisted. Two consoles are booted
	// over ONE registry db and ONE pinned project directory — both owned by this
	// test, so they survive the first console's disposal (which kills only what
	// that handle itself created).
	const dbDir = mkdtempSync(path.join(tmpdir(), 'factory-console-e2e-db-'));
	specOwnedDirs.push(dbDir);
	const dbPath = path.join(dbDir, 'console.db');
	const pinnedDir = ownedCopy('with_run_state');
	const otherDir = ownedCopy('minimal');
	const pinnedRoot = realpathSync(pinnedDir);

	let first: DedicatedConsole | undefined;
	let second: DedicatedConsole | undefined;
	try {
		first = await start(undefined, { projectDir: pinnedDir, dbPath });
		const other = await registerProjectDir(first, otherDir);

		await test.step('a switch in the first session persists to the registry', async () => {
			await selectProject(first!, other.id);
			expect(await currentSelectionId(first!)).toBe(other.id);
			expect(await servedRoot(first!)).toBe(other.path);
		});

		// Only the PROCESS goes away — the registry db and both project
		// directories are this test's, and outlive it.
		await first.dispose();
		first = undefined;

		second = await start(undefined, { projectDir: pinnedDir, dbPath });

		await test.step('the next boot serves the PATH it was given, not the persisted selection', async () => {
			// The pin is the session's INITIAL selection, so `factory-console PATH`
			// never silently serves a different project than the one it printed.
			expect(await currentSelectionId(second!)).toBe(SESSION_PROJECT_ID);
			expect(await servedRoot(second!)).toBe(pinnedRoot);
		});

		await test.step('and switching still takes effect in that same session', async () => {
			// The other half of the rule: if the pin simply WON, v3.0's headline
			// feature would be inert in the only invocation that ships.
			await selectProject(second!, other.id);
			expect(await currentSelectionId(second!)).toBe(other.id);
			expect(await servedRoot(second!)).toBe(other.path);
		});
	} finally {
		await first?.dispose();
		await second?.dispose();
	}
});
