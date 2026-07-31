import { test, expect } from '@playwright/test';
import { start, type DedicatedConsole } from './lib/dedicated-console';

// The guardrails around the v2 write path, end to end in a real browser: a
// destructive delete must go through an explicit confirmation, and a ticket a
// factory lane already owns (run-state past `todo`) must show its edit/delete
// affordances inert behind an explanatory banner.
//
// The delete case is a REAL write (the manifest entry and the markdown file go
// away), so — exactly like editing.spec.ts — this can never run against the
// shared fixture (contractually read-only) or the shared console booted by
// global-setup. It boots a DEDICATED console over a private temp COPY of
// `with_run_state` (see ./lib/dedicated-console), navigates by that console's
// OWN absolute base URL (NOT `use.baseURL`, which points at the shared one), and
// disposes it afterward; the copy is the sole thing written.
//
// Writes are authorized the way the SPA expects: the console mints a per-session
// token and prints it to stderr, the harness parses it out, and the delete test
// seeds it into `sessionStorage` under the store's own key BEFORE any page
// script runs — indistinguishable, to the app, from a human having pasted it
// into `WriteTokenPrompt`.

// MIRRORS `STORAGE_KEY` in `frontend/src/lib/stores/writeToken.ts` — the key that
// module hydrates from at import. Keep in sync with it.
const WRITE_TOKEN_STORAGE_KEY = 'factory-console:writeToken';

// Assigned in beforeAll; read by the tests and afterAll. Left undefined if
// beforeAll's `start()` throws, so afterAll guards before disposing.
let dedicated: DedicatedConsole | undefined;

test.beforeAll(async () => {
	dedicated = await start();
});

test.afterAll(async () => {
	// Guard: dispose even if beforeAll partially failed (start() cleans up its
	// own leaks on throw, so `dedicated` is only set on a fully-booted handle).
	await dedicated?.dispose();
});

test('guardrails: deleting CAD-140 needs a confirmation and cancelling leaves it intact', async ({
	page
}) => {
	// `start()` resolved before this test ran, so the handle is present.
	const handle = dedicated!;

	const confirmDialog = page.getByRole('dialog', { name: 'Delete ticket?' });
	// `exact` because accessible-name matching is substring-based by default, and
	// the confirmation's own "Delete ticket" button is mounted while it is open.
	const deleteButton = page.getByRole('button', { name: 'Delete', exact: true });

	await test.step('the session is authorized before anything loads', async () => {
		// `addInitScript` runs before any page script, so the store hydrates from
		// this on the SPA's very first load and the delete never stops to ask for a
		// token (`startDelete` opens the confirmation only when one is already held).
		await page.addInitScript(
			([key, token]) => sessionStorage.setItem(key, token),
			[WRITE_TOKEN_STORAGE_KEY, handle.writeToken]
		);
	});

	await test.step('opening CAD-140 offers the delete affordance', async () => {
		// Navigate by the DEDICATED console's absolute base URL — `use.baseURL`
		// points at the shared console over the untouched shared fixture.
		await page.goto(handle.baseURL + '/');
		await expect(page.getByRole('heading', { name: 'Tickets', level: 1 })).toBeVisible();
		await page.getByRole('link', { name: 'CAD-140', exact: true }).click();
		await expect(page).toHaveURL(/\/tickets\/CAD-140$/);
		// CAD-140's run-state is `todo`, the one state `isEditable` allows, so the
		// button is live rather than gated behind EditGate's read-only banner.
		await expect(deleteButton).toBeEnabled();
	});

	await test.step('delete asks before it destroys, and cancelling destroys nothing', async () => {
		await deleteButton.click();
		// The confirmation names the ticket it would remove — nothing has been sent
		// to `DELETE /api/v1/tickets/{id}` yet.
		await expect(confirmDialog).toBeVisible();
		await expect(confirmDialog.getByText('CAD-140')).toBeVisible();
		await confirmDialog.getByRole('button', { name: 'Cancel' }).click();
		// Backing out leaves the route exactly where it was, still showing the ticket.
		await expect(confirmDialog).toBeHidden();
		await expect(page).toHaveURL(/\/tickets\/CAD-140$/);
		await expect(
			page.getByRole('heading', { name: 'Habit heatmap calendar view', level: 1 }).first()
		).toBeVisible();
	});

	await test.step('confirming deletes the ticket and hands the user back to the list', async () => {
		await deleteButton.click();
		await expect(confirmDialog).toBeVisible();
		await confirmDialog.getByRole('button', { name: 'Delete ticket' }).click();
		// The route it was rendering is gone, so `confirmDelete` leaves for the list
		// with `invalidateAll`, forcing the load to re-run without the deleted row.
		await expect(page).toHaveURL(/\/$/);
		await expect(page.getByRole('heading', { name: 'Tickets', level: 1 })).toBeVisible();
		await expect(page.getByRole('link', { name: 'CAD-140', exact: true })).toHaveCount(0);
	});
});

test('guardrails: CAD-118 is read-only, with the edit and delete controls disabled', async ({
	page
}) => {
	const handle = dedicated!;

	await test.step('opening CAD-118 shows the read-only banner', async () => {
		// No write token is seeded: nothing here writes, and the disabled state is a
		// run-state gate, not an authorization one.
		await page.goto(handle.baseURL + '/');
		await page.getByRole('link', { name: 'CAD-118', exact: true }).click();
		await expect(page).toHaveURL(/\/tickets\/CAD-118$/);
		// EditGate renders its `note` ONLY when `isEditable` is false, and names the
		// RAW run-state — `ready`, which a factory lane already owns.
		const banner = page.getByRole('note');
		await expect(banner).toBeVisible();
		await expect(banner).toContainText('Read-only.');
		await expect(banner).toContainText('ready');
		await expect(banner).toContainText('editing and deleting are disabled');
	});

	await test.step('the write affordances the banner explains are inert', async () => {
		// The buttons share the banner's one predicate (`isEditable`), so what it
		// explains is exactly what is disabled — both of them.
		await expect(page.getByRole('button', { name: 'Edit', exact: true })).toBeDisabled();
		await expect(page.getByRole('button', { name: 'Delete', exact: true })).toBeDisabled();
		// Not force-clicked: a disabled button is inert by definition, and forcing
		// past that would assert about a click the browser never delivers. What is
		// worth asserting is that no editing surface is reachable on this route at
		// all — neither modal is mounted, so there is no other way in.
		await expect(page.getByRole('dialog')).toHaveCount(0);
	});
});
