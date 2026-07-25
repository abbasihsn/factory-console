import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect } from '@playwright/test';

// Captures the README screenshots from the REAL UI served by the harness — a
// real factory-console booted in global-setup on the `with_run_state` fixture
// (use.baseURL points at it). The MVP views (list / detail / deps) plus the v1
// views (graph / roadmap / search) and the live-update indicator. Kept separate
// from the happy-path spec so a broken screenshot pipeline never masks an e2e
// regression.
//
// Like happy-path, the SPA is client-side routed with ssr=false, so every
// navigation is by CLICK/`goto` starting from `/` — deep-link reloads of
// /tickets/... aren't served by the static mount. Locators are role/label/text
// based and each screenshot is taken only AFTER the web-first visibility
// assertion for its page has passed, so no capture is blank or partial. We do
// NOT wait for `networkidle`: the layout opens a long-lived `/api/v1/events` SSE
// stream (the live-update feature) that never lets the network go idle — the
// per-page visibility assertions (and, for /graph, the `window.__cy` edge poll)
// are the deterministic capture gate instead, matching graph.spec/search.spec.
//
// These are plain `page.screenshot({ path })` captures, NOT Playwright visual
// comparisons (toMatchSnapshot) — the PNGs are docs artifacts, not a baseline.

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
