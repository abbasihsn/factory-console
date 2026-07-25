import { test, expect } from '@playwright/test';

// One end-to-end walk of the MVP happy path against the `with_run_state` fixture,
// served by a real factory-console booted in global-setup (use.baseURL points at
// it). The SPA is client-side routed with ssr=false, so every navigation is by
// CLICK starting from `/` — deep-link reloads of /tickets/... aren't served by the
// static mount. Locators are role/label/text based (accessible, non-flaky) and
// lean on Playwright auto-waiting; the search box is debounced but web-first
// assertions retry, so no fixed sleeps are needed.
test('happy path: list -> filter -> ticket -> deps -> dep ticket', async ({ page }) => {
	await test.step('index lists the fixture tickets', async () => {
		await page.goto('/');
		await expect(page.getByRole('heading', { name: 'Tickets', level: 1 })).toBeVisible();
		await expect(page.getByRole('link', { name: 'CAD-125' })).toBeVisible();
		await expect(page.getByRole('link', { name: 'CAD-100' })).toBeVisible();
	});

	await test.step('searching "Daily" narrows the list to CAD-125', async () => {
		// Scope to the FiltersBar box in <main>: the header banner's NavSearch box
		// carries the same role+name ("Search tickets"), so an unscoped locator is
		// strict-mode ambiguous on the index route.
		await page.getByRole('main').getByRole('searchbox', { name: 'Search tickets' }).fill('Daily');
		// The query re-filters server-side over id + title; only CAD-125 ("Daily
		// check-in…") survives, so the CAD-100 link disappears.
		await expect(page.getByRole('link', { name: 'CAD-100' })).toHaveCount(0);
		await expect(page.getByRole('link', { name: 'CAD-125' })).toBeVisible();
	});

	await test.step('clicking CAD-125 opens its detail page', async () => {
		await page.getByRole('link', { name: 'CAD-125' }).click();
		await expect(page).toHaveURL(/\/tickets\/CAD-125$/);
		// The page header h1 and the MarkdownBody (whose body starts with a top-level
		// "# Daily check-in…" heading) both carry the title — assert the first (header).
		await expect(
			page.getByRole('heading', { name: 'Daily check-in REST endpoints', level: 1 }).first()
		).toBeVisible();
		// StatusBadge renders the raw status; RunStateBadge humanizes in-flight.
		await expect(page.getByText('in_progress')).toBeVisible();
		await expect(page.getByText('In flight')).toBeVisible();
		// MarkdownBody prose rendered from the ticket body.
		await expect(page.getByText('makes Cadence usable')).toBeVisible();
	});

	await test.step('opening the dep neighborhood shows the direct deps', async () => {
		await page.getByRole('link', { name: 'View dep neighborhood' }).click();
		await expect(page).toHaveURL(/\/tickets\/CAD-125\/deps$/);
		await expect(page.getByRole('heading', { name: 'Deps for CAD-125', level: 1 })).toBeVisible();
		await expect(page.getByRole('link', { name: 'CAD-100' })).toBeVisible();
	});

	await test.step('clicking the CAD-100 dep lands on its detail page', async () => {
		await page.getByRole('link', { name: 'CAD-100' }).click();
		await expect(page).toHaveURL(/\/tickets\/CAD-100$/);
		// Same header/MarkdownBody duplicate-h1 shape as CAD-125 — assert the header.
		await expect(
			page
				.getByRole('heading', { name: 'Habit schema and append-only event store', level: 1 })
				.first()
		).toBeVisible();
	});
});
