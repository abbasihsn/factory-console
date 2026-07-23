import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect } from '@playwright/test';

// Captures the three README screenshots (list / detail / deps) from the REAL UI
// served by the harness — a real factory-console booted in global-setup on the
// `with_run_state` fixture (use.baseURL points at it). Kept separate from the
// happy-path spec so a broken screenshot pipeline never masks an e2e regression.
//
// Like happy-path, the SPA is client-side routed with ssr=false, so every
// navigation is by CLICK starting from `/` — deep-link reloads of /tickets/...
// aren't served by the static mount. Locators are role/label/text based and
// each screenshot is taken only AFTER the web-first visibility assertion for its
// page has passed (plus a networkidle wait), so no capture is blank or partial.
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
		await page.waitForLoadState('networkidle');
		await page.screenshot({ path: shot('list.png') });
	});

	await test.step('capture the CAD-125 detail page', async () => {
		await page.getByRole('link', { name: 'CAD-125' }).click();
		await expect(page).toHaveURL(/\/tickets\/CAD-125$/);
		// Header h1 and MarkdownBody both carry the title — assert the first (header).
		await expect(
			page.getByRole('heading', { name: 'Daily check-in REST endpoints', level: 1 }).first()
		).toBeVisible();
		await page.waitForLoadState('networkidle');
		await page.screenshot({ path: shot('detail.png') });
	});

	await test.step('capture the CAD-125 dep neighborhood', async () => {
		await page.getByRole('link', { name: 'View dep neighborhood' }).click();
		await expect(page).toHaveURL(/\/tickets\/CAD-125\/deps$/);
		await expect(page.getByRole('heading', { name: 'Deps for CAD-125', level: 1 })).toBeVisible();
		await expect(page.getByRole('link', { name: 'CAD-100' })).toBeVisible();
		await page.waitForLoadState('networkidle');
		await page.screenshot({ path: shot('deps.png') });
	});
});
