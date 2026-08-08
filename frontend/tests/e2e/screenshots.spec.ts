import path from 'node:path';
import { renameSync, rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test, expect } from '@playwright/test';
import { copyFixture, start, registerProjectDir, type DedicatedConsole } from './lib/dedicated-console';

// Captures the README screenshots from the REAL UI served by the harness — a
// real factory-console booted in global-setup on the `with_run_state` fixture
// (use.baseURL points at it). The MVP views (list / detail / deps) plus the v1
// views (graph / roadmap / search) and the live-update indicator. Kept separate
// from the happy-path spec so a broken screenshot pipeline never masks an e2e
// regression.
//
// Like happy-path, the SPA is client-side routed with ssr=false. Top-level
// routes (`/`, `/graph`, `/roadmap`) are reached by `goto` — the server's SPA
// static mount falls back to index.html for any non-API client route, so a
// deep-link load IS served — while in-app destinations (a ticket detail, its
// deps, the /search results) are reached by CLICK/submit from `/`, exactly as a
// user would. Locators are role/label/text
// based and each screenshot is taken only AFTER the web-first visibility
// assertion for its page has passed, so no capture is blank or partial. We do
// NOT wait for `networkidle`: the layout opens a long-lived `/api/v1/events` SSE
// stream (the live-update feature) that never lets the network go idle — the
// per-page visibility assertions (and, for /graph, the `window.__cy` edge poll)
// are the deterministic capture gate instead, matching graph.spec/search.spec.
//
// These are plain `page.screenshot({ path })` captures, NOT Playwright visual
// comparisons (toMatchSnapshot) — the PNGs are docs artifacts, not a baseline.
//
// The v3.0 captures at the bottom are the one exception to "the shared console
// serves every shot": the switcher and a populated `/projects` table only exist
// where the console tracks more than one project, which the single-project
// shared console cannot show — see that block's own comment.

// frontend/tests/e2e -> the gitignored capture dir alongside this spec. Resolved
// from the module URL (the package is `"type": "module"`, so no __dirname) so the
// path is correct regardless of the cwd Playwright runs from. `page.screenshot`
// auto-creates the directory.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const SCREENSHOTS_DIR = path.join(HERE, '__screenshots__');
const shot = (name: string): string => path.join(SCREENSHOTS_DIR, name);

test('screenshots: capture list, detail, and deps neighborhood PNGs', async ({ page }) => {
	await test.step('capture the ticket list', async () => {
		await page.goto('/');
		await expect(page.getByRole('heading', { name: 'Tickets', level: 1 })).toBeVisible();
		await expect(page.getByRole('link', { name: 'CAD-125' })).toBeVisible();
		await page.screenshot({ path: shot('list.png') });
	});

	await test.step('capture the CAD-125 detail page', async () => {
		await page.getByRole('link', { name: 'CAD-125' }).click();
		await expect(page).toHaveURL(/\/tickets\/CAD-125$/);
		// Header h1 and MarkdownBody both carry the title — assert the first (header).
		await expect(
			page.getByRole('heading', { name: 'Daily check-in REST endpoints', level: 1 }).first()
		).toBeVisible();
		await page.screenshot({ path: shot('detail.png') });
	});

	await test.step('capture the CAD-125 dep neighborhood', async () => {
		await page.getByRole('link', { name: 'View dep neighborhood' }).click();
		await expect(page).toHaveURL(/\/tickets\/CAD-125\/deps$/);
		await expect(page.getByRole('heading', { name: 'Deps for CAD-125', level: 1 })).toBeVisible();
		await expect(page.getByRole('link', { name: 'CAD-100' })).toBeVisible();
		await page.screenshot({ path: shot('deps.png') });
	});
});

// CAD-125 depends on CAD-118 → DepGraph builds a cytoscape edge `${source}->${target}`.
// Gating on this edge (via the documented `window.__cy` core, set only after the
// async cytoscape import in onMount) proves the opaque canvas has actually painted
// before we capture — the same render gate graph.spec.ts uses (bounded poll, not a
// sleep).
const DEP_EDGE_ID = 'CAD-125->CAD-118';

test('screenshots: capture the graph, roadmap, search, and live-update PNGs', async ({ page }) => {
	await test.step('capture the /graph dependency graph', async () => {
		await page.goto('/graph');
		await expect(page.getByRole('heading', { name: 'Dependency graph', level: 1 })).toBeVisible();
		// The accessible node nav lists one <a> per fixture node — gate on all 6 so the
		// graph model is fully built before the capture.
		const nodeNav = page.getByRole('navigation', { name: 'Ticket dependency nodes' });
		await expect(nodeNav.getByRole('link')).toHaveCount(6);
		// Then gate on the canvas actually painting: poll (bounded, not a sleep) until
		// the CAD-125 → CAD-118 edge resolves in the cytoscape core.
		await expect
			.poll(
				() =>
					page.evaluate((edgeId) => {
						const core = (
							window as unknown as { __cy?: { getElementById(id: string): { length: number } } }
						).__cy;
						return core?.getElementById(edgeId).length === 1;
					}, DEP_EDGE_ID),
				{ timeout: 10_000 }
			)
			.toBe(true);
		await page.screenshot({ path: shot('graph.png') });
	});

	await test.step('capture the /roadmap milestone view', async () => {
		await page.goto('/roadmap');
		// `exact` so the page h1 "Roadmap" doesn't also match the rendered ROADMAP.md
		// body title "Cadence Roadmap" (a second, markdown-emitted <h1>).
		await expect(
			page.getByRole('heading', { name: 'Roadmap', level: 1, exact: true })
		).toBeVisible();
		// Concrete rendered content — a milestone section's ticket mini-row link — so the
		// shot isn't taken on an empty roadmap shell.
		await expect(page.getByRole('link', { name: 'CAD-100' })).toBeVisible();
		await page.screenshot({ path: shot('roadmap.png') });
	});

	await test.step('capture the /search results', async () => {
		// Navigate the way a user does: the global search box in the header banner
		// (scoped to the banner to disambiguate the index route's FiltersBar box of the
		// same name). "idempotent" is a body-only term hitting exactly CAD-125 + CAD-118.
		await page.goto('/');
		const globalSearch = page
			.getByRole('banner')
			.getByRole('searchbox', { name: 'Search tickets' });
		await globalSearch.fill('idempotent');
		await globalSearch.press('Enter');
		// Gate on the /search results page ITSELF — its own <h1> and exactly the two
		// body-match results. The list page at `/` also links CAD-125/CAD-118, so those
		// links alone wouldn't prove the SPA navigation to /search actually landed.
		await expect(page).toHaveURL(/\/search\?q=idempotent$/);
		await expect(page.getByRole('heading', { name: 'Search', level: 1 })).toBeVisible();
		await expect(page.getByRole('link', { name: /^CAD-\d+$/ })).toHaveCount(2);
		await page.screenshot({ path: shot('search.png') });
	});

	await test.step('capture the live-update indicator in its connected state', async () => {
		// The LiveIndicator pill sits in the layout on every route. On the read-only
		// shared fixture (no watcher mutations) it settles at a steady "Live" once the
		// SSE stream connects — a web-first wait, no fixture-mutation dance. Capture just
		// the pill element so the shot documents the indicator itself.
		await page.goto('/');
		await expect(page.getByRole('heading', { name: 'Tickets', level: 1 })).toBeVisible();
		const livePill = page.getByText('Live', { exact: true });
		await expect(livePill).toBeVisible();
		await livePill.screenshot({ path: shot('live.png') });
	});
});

// v3.0's two surfaces. Both need a console that tracks MORE THAN ONE project:
// `ProjectSwitcher` renders nothing below two rows (single-project mode looks
// exactly as it did before it existed), and a `/projects` table with one row
// would document neither the registry nor what `Select`/`Remove` are for. The
// shared console booted by global-setup is single-project and contractually
// read-only, so — exactly as multi-project.spec.ts does — these captures boot a
// DEDICATED console over private fixture copies (`with_run_state` pinned as the
// session project, `minimal` registered against it over the live API) and
// navigate by THAT console's own absolute base URL, never `use.baseURL`.
//
// Scoped to its own describe so the second console is booted only for these
// shots and disposed with them; the captures above keep using the shared one.
//
// `copyFixture`'s own temp dir is named `factory-console-e2e-<random>` — fine for
// every other spec, which never shows the basename to a human, but here it IS
// the row name a screenshot documents. Renaming the copy to the fixture's own
// name (its parent stays the random mkdtemp dir; only the FINAL component,
// which `default_project_name` reads, changes) makes the captions true and
// stops every regen from rewriting both PNGs over a name nobody chose.
function withStableName(dir: string, name: string): string {
	const stable = path.join(path.dirname(dir), name);
	renameSync(dir, stable);
	return stable;
}

test.describe('screenshots: the multi-project console', () => {
	// Assigned in beforeAll; left undefined if setup throws, so afterAll guards
	// before disposing. Both dirs are caller-supplied (`start`'s `projectDir`
	// option, `registerProjectDir` directly) specifically so THIS block can name
	// them, which also means neither is reaped by `dedicated.dispose()` — this
	// block owns and removes both itself.
	let dedicated: DedicatedConsole | undefined;
	let sessionDir: string | undefined;
	let registeredDir: string | undefined;

	test.beforeAll(async () => {
		sessionDir = withStableName(copyFixture('with_run_state'), 'with_run_state');
		registeredDir = withStableName(copyFixture('minimal'), 'minimal');
		dedicated = await start('with_run_state', { projectDir: sessionDir });
		await registerProjectDir(dedicated, registeredDir);
	});

	test.afterAll(async () => {
		// `dispose` reaps the child and its private registry db, but neither fixture
		// copy — both are caller-owned (see `beforeAll`) — so this block removes them.
		await dedicated?.dispose();
		if (sessionDir) rmSync(sessionDir, { recursive: true, force: true });
		if (registeredDir) rmSync(registeredDir, { recursive: true, force: true });
	});

	test('screenshots: capture the project switcher and the /projects registry PNGs', async ({
		page
	}) => {
		const handle = dedicated!;

		await test.step('capture the header switcher over a two-project registry', async () => {
			await page.goto(handle.baseURL + '/');
			await expect(page.getByRole('heading', { name: 'Tickets', level: 1 })).toBeVisible();
			// `exact` so it can't match `AddProjectForm`'s "Project path" box, the same
			// disambiguation multi-project.spec.ts makes.
			const switcher = page.getByRole('combobox', { name: 'Project', exact: true });
			await expect(switcher).toBeVisible();
			// Both project rows plus the trailing "Manage projects…" entry: the control
			// is present AND populated, which is what makes this the multi-project header
			// rather than a header that happens to render a dropdown.
			await expect(switcher.locator('option')).toHaveCount(3);
			// Captured CLOSED, deliberately. A native `<select>`'s open list is drawn by
			// the platform above the page, not into the page, so headless Chromium does
			// not composite it into a screenshot at all — forcing it open would produce
			// a PNG indistinguishable from this one while implying the docs show
			// something they don't. Captured on the header element itself, so the shot
			// documents the switcher in its place beside the served path and the
			// Projects nav link.
			await page.getByRole('banner').screenshot({ path: shot('switcher.png') });
		});

		await test.step('capture the /projects registry management page', async () => {
			await page.goto(handle.baseURL + '/projects');
			await expect(page.getByRole('heading', { name: 'Projects', level: 1 })).toBeVisible();
			// Gate on the TABLE, not the shell: one row per tracked project — the pinned
			// session row and the registered one — so the shot is never of the empty
			// registry panel or of a page whose load has not landed.
			await expect(page.locator('[data-testid^="project-row-"]')).toHaveCount(2);
			await page.screenshot({ path: shot('projects.png') });
		});
	});
});
