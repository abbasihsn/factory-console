import { test, expect } from '@playwright/test';

// Browser-level acceptance for the v1 global search box (NavSearch, rendered in
// TopBar inside the <header> banner on every route) and its `/search` results
// page, served by a real factory-console booted in global-setup on the
// read-only `with_run_state` fixture (use.baseURL points at it). This spec
// proves search is genuinely FULL-TEXT: the term "idempotent" lives only in the
// BODIES of CAD-125 and CAD-118 (never in any title), so a hit for it can only
// come from a body match — and, since it hits two tickets, it also proves the
// results page lists MORE THAN ONE match. Each result is a link to
// `/tickets/{id}`, which we follow to the detail page; a gibberish query then
// exercises the no-match empty state.
//
// Banner-scoping rationale: on the index route `/` the list FiltersBar ALSO
// renders a searchbox named "Search tickets" (the MVP id/title list filter),
// so a bare `getByRole('searchbox', { name: 'Search tickets' })` is a
// strict-mode violation there. The GLOBAL box we want lives in the <header>
// (ARIA role `banner`), so we always scope to it — never relying on the fragile
// placeholder-ellipsis difference. NavSearch navigates only on FORM SUBMIT and
// is not debounced, so we `.fill(term).press('Enter')` to trigger a search;
// filling alone does nothing. Locators are role/label/text based and every
// assertion is web-first (auto-retrying), so the navigation + `/api/v1/search`
// round-trip is absorbed with no fixed sleeps.

// The global search box: the searchbox inside the header banner, disambiguated
// from the index route's FiltersBar box of the same accessible name.
const globalSearch = (page: import('@playwright/test').Page) =>
	page.getByRole('banner').getByRole('searchbox', { name: 'Search tickets' });

// Any ticket-id result link on the /search page ("CAD-125", "CAD-118", …). On
// /search the only ticket-id links are results (the banner links are
// Home/Graph/Roadmap), so this matches results and nothing else.
const resultLinks = (page: import('@playwright/test').Page) =>
	page.getByRole('link', { name: /^CAD-\d+$/ });

test('search: global box full-text-matches ticket bodies, links to detail, and shows the empty state', async ({
	page
}) => {
	await test.step('a body-only term surfaces its ticket via full-text match', async () => {
		await page.goto('/');
		// "idempotent" is in CAD-125's body but in no ticket title, so a hit proves
		// the match is full-text (body), not the MVP's id/title filter.
		await globalSearch(page).fill('idempotent');
		await globalSearch(page).press('Enter');
		const cad125 = page.getByRole('link', { name: 'CAD-125' });
		await expect(cad125).toBeVisible();
		await expect(cad125).toHaveAttribute('href', '/tickets/CAD-125');
	});

	await test.step('the shared body term returns more than one result', async () => {
		// "idempotent" also appears in CAD-118's body, so the results page lists at
		// least two matches — CAD-118 alongside CAD-125.
		await expect(page.getByRole('link', { name: 'CAD-118' })).toBeVisible();
		await expect(resultLinks(page)).toHaveCount(2);
	});

	await test.step('clicking a result opens that ticket detail page', async () => {
		await page.getByRole('link', { name: 'CAD-125' }).click();
		await expect(page).toHaveURL(/\/tickets\/CAD-125$/);
		// The page header h1 and the MarkdownBody both carry the title — assert the first.
		await expect(
			page.getByRole('heading', { name: 'Daily check-in REST endpoints', level: 1 }).first()
		).toBeVisible();
	});

	await test.step('a no-match query shows the empty state and zero results', async () => {
		await page.goto('/');
		await globalSearch(page).fill('zzznomatchqqq');
		await globalSearch(page).press('Enter');
		await expect(resultLinks(page)).toHaveCount(0);
		// The empty state renders `No tickets match "<q>".` with curly quotes —
		// assert a stable substring rather than the curly-quote characters.
		await expect(page.getByText('No tickets match')).toBeVisible();
	});
});
