import { test, expect } from '@playwright/test';
import { start, type DedicatedConsole } from './lib/dedicated-console';

// The v2 write path, end to end in a real browser: edit an existing ticket and
// create a new one, each through form → dry-run → diff review → save. These are
// REAL writes to the manifest and the ticket markdown, so they can never run
// against the shared fixture (contractually read-only) or the shared console
// booted by global-setup — every other spec reads that same project and would
// see the mutations. So this boots a DEDICATED console over a private temp COPY
// of `with_run_state` (see ./lib/dedicated-console), navigates to it by its OWN
// absolute base URL (NOT `use.baseURL`, which points at the shared console), and
// disposes it afterward; the copy is the sole thing written.
//
// Writes are authorized the way the SPA expects: the console mints a per-session
// token and prints it to stderr, the harness parses it out, and each test seeds
// it into `sessionStorage` under the store's own key BEFORE any page script runs
// — indistinguishable, to the app, from a human having pasted it into
// `WriteTokenPrompt`.

// MIRRORS `STORAGE_KEY` in `frontend/src/lib/stores/writeToken.ts` — the key that
// module hydrates from at import. Keep in sync with it.
const WRITE_TOKEN_STORAGE_KEY = 'factory-console:writeToken';

// The id created by the create case. Free in the fixture (which holds CAD-100,
// -118, -125, -131, -140, -152), follows its CAD-nnn convention, and passes
// `validateTicketForm`'s `TICKET_ID_PATTERN`.
const NEW_TICKET_ID = 'CAD-160';

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

test('editing: changing CAD-140s body previews a diff and persists on save', async ({ page }) => {
	// `start()` resolved before this test ran, so the handle is present.
	const handle = dedicated!;

	// The one line the whole test is keyed on: it must show up as an ADDED diff
	// line in the review dialog, then as rendered prose on the detail page.
	const newBody = [
		'# Habit heatmap calendar view',
		'',
		'Rewritten by the editing e2e so the diff has something unmistakable in it.'
	].join('\n');

	await test.step('the session is authorized before anything loads', async () => {
		// `addInitScript` runs before any page script, so the store hydrates from
		// this on the SPA's very first load and no prompt is ever raised.
		await page.addInitScript(
			([key, token]) => sessionStorage.setItem(key, token),
			[WRITE_TOKEN_STORAGE_KEY, handle.writeToken]
		);
	});

	await test.step('opening CAD-140 offers the edit affordance', async () => {
		// Navigate by the DEDICATED console's absolute base URL — `use.baseURL`
		// points at the shared console over the untouched shared fixture.
		await page.goto(handle.baseURL + '/');
		await expect(page.getByRole('heading', { name: 'Tickets', level: 1 })).toBeVisible();
		await page.getByRole('link', { name: 'CAD-140', exact: true }).click();
		await expect(page).toHaveURL(/\/tickets\/CAD-140$/);
		// CAD-140's run-state is `todo`, the one state `isEditable` allows, so the
		// button is live rather than gated behind EditGate's read-only banner.
		await page.getByRole('button', { name: 'Edit' }).click();
	});

	const editDialog = page.getByRole('dialog', { name: 'Edit CAD-140' });
	const reviewDialog = page.getByRole('dialog', { name: 'Review changes' });

	await test.step('submitting the rewritten body opens the diff review', async () => {
		await expect(editDialog).toBeVisible();
		// The body field is CodeMirror's contenteditable surface, named by the
		// `ariaLabel` MarkdownEditor lands on it.
		await editDialog.getByRole('textbox', { name: 'Ticket body' }).fill(newBody);
		await editDialog.getByRole('button', { name: 'Save changes' }).click();
	});

	await test.step('the diff shows the rewrite before anything is written', async () => {
		await expect(reviewDialog).toBeVisible();
		// One span per diff line, so these regexes pin the +/- prefix: the new
		// sentence is added and the old opening line is removed.
		await expect(reviewDialog.getByText(/^\+.*Rewritten by the editing e2e/).first()).toBeVisible();
		await expect(reviewDialog.getByText(/^-.*A year of a habit at a glance/).first()).toBeVisible();
	});

	await test.step('saving applies the edit and the detail page re-renders it', async () => {
		await reviewDialog.getByRole('button', { name: 'Save' }).click();
		// Both dialogs come down on a successful apply: `applyEdit` resets the write
		// state and the route's `handleEditSaved` closes the form and `invalidateAll()`s.
		await expect(reviewDialog).toBeHidden();
		await expect(editDialog).toBeHidden();
		// The re-run load re-fetches the ticket, so MarkdownBody now renders the body
		// as it was actually written to disk.
		await expect(page.getByText('Rewritten by the editing e2e')).toBeVisible();
	});
});

test('editing: creating a ticket previews a diff and lands it in the list', async ({ page }) => {
	const handle = dedicated!;

	await test.step('the session is authorized before anything loads', async () => {
		await page.addInitScript(
			([key, token]) => sessionStorage.setItem(key, token),
			[WRITE_TOKEN_STORAGE_KEY, handle.writeToken]
		);
	});

	await test.step('the list offers the create route', async () => {
		await page.goto(handle.baseURL + '/');
		await page.getByRole('link', { name: 'New ticket' }).click();
		await expect(page).toHaveURL(/\/tickets\/new$/);
		await expect(page.getByRole('heading', { name: 'New ticket', level: 1 })).toBeVisible();
	});

	await test.step('filling the create form opens the diff review', async () => {
		await page.getByRole('textbox', { name: 'Ticket id' }).fill(NEW_TICKET_ID);
		await page.getByRole('textbox', { name: 'Title' }).fill('Streak recovery grace period');
		await page.getByRole('textbox', { name: 'Depends on' }).fill('CAD-125');
		await page.getByRole('textbox', { name: 'Provides' }).fill('A one-day grace window');
		await page.getByRole('textbox', { name: 'Ticket body' }).fill('Created by the editing e2e.');
		await page.getByRole('button', { name: 'Create ticket' }).click();
	});

	const reviewDialog = page.getByRole('dialog', { name: 'Review changes' });

	await test.step('the diff shows the new ticket before anything is written', async () => {
		await expect(reviewDialog).toBeVisible();
		await expect(reviewDialog.getByText(/^\+.*Created by the editing e2e/).first()).toBeVisible();
	});

	await test.step('saving creates the ticket and navigates to it', async () => {
		await reviewDialog.getByRole('button', { name: 'Save' }).click();
		// `applyCreate` hands the user straight to the server's id for what it wrote.
		await expect(page).toHaveURL(new RegExp(`/tickets/${NEW_TICKET_ID}$`));
		await expect(
			page.getByRole('heading', { name: 'Streak recovery grace period', level: 1 })
		).toBeVisible();
	});

	await test.step('the new ticket is in the list', async () => {
		// The header banner's Home link — the detail route has no back-link of its own.
		await page.getByRole('link', { name: 'Home' }).click();
		await expect(page).toHaveURL(/\/$/);
		await expect(page.getByRole('link', { name: NEW_TICKET_ID, exact: true })).toBeVisible();
	});
});
