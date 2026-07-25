import { test, expect } from '@playwright/test';
import { start, type DedicatedConsole } from './lib/dedicated-console';

// The hardest v1 e2e: with the watcher running, mutating a project's run-state
// on disk must refresh the ALREADY-OPEN view via the `/api/v1/events` SSE
// stream. The shared fixtures are read-only and the shared console is
// single-worker, so this test can't mutate them in place. It boots a DEDICATED
// second console (watcher on) against a private temp COPY of `with_run_state`
// (see ./lib/dedicated-console), navigates to it by its OWN absolute base URL
// (NOT `use.baseURL`, which points at the shared console), moves CAD-140's
// run-state marker on the copy, and asserts the badge flips from "To do" to
// "In flight" — the refresh path being: watcher sees the change → SSE event →
// the layout's `invalidateAll()` re-runs the index loader → the row re-renders.
// The copy is the sole writer; the shared fixture/console are never touched.

// Assigned in beforeAll; read by the test and afterAll. Left undefined if
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

test('live update: moving CAD-140 run-state refreshes the open list via SSE', async ({ page }) => {
	// `start()` resolved before this test ran, so the handle is present.
	const handle = dedicated!;

	// Register the SSE-connection gate BEFORE navigating so we can't miss the
	// stream opening — this closes the connect-vs-mutate race.
	const sseConnected = page.waitForResponse((response) =>
		response.url().includes('/api/v1/events')
	);

	// Navigate by the DEDICATED console's absolute base URL — `use.baseURL`
	// points at the shared console, which serves the untouched shared fixture.
	await page.goto(handle.baseURL + '/');
	await expect(page.getByRole('heading', { name: 'Tickets', level: 1 })).toBeVisible();

	// Scope to CAD-140's row (a `<li>` in the index `<ul>`, rendered by
	// TicketRow) so we don't match the OTHER todo tickets' badges. CAD-140 starts
	// in `todo` → its RunStateBadge reads "To do".
	const cad140Row = () =>
		page.getByRole('listitem').filter({
			has: page.getByRole('link', { name: 'CAD-140', exact: true })
		});
	await expect(cad140Row().getByText('To do', { exact: true })).toBeVisible();

	// Determinism gate: the SSE stream must be connected before we mutate, or the
	// watcher event could fire before the browser is subscribed and be missed.
	await sseConnected;

	// Mutate the private copy (sole writer): move CAD-140's marker todo →
	// in-flight. The watcher detects it and emits an SSE event.
	handle.moveRunState('CAD-140', 'todo', 'in-flight');

	// The refresh IS the wait: a bounded, web-first retry. On the SSE event the
	// layout calls `invalidateAll()`, the index loader re-runs, and the row
	// re-renders with the badge now reading "In flight". Re-derive the row each
	// poll (a fresh locator query) since the loader replaces the DOM nodes.
	await expect(cad140Row().getByText('In flight', { exact: true })).toBeVisible({
		timeout: 10_000
	});
});
