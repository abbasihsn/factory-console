import { render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Fully mock the API barrel so `load` never touches the network. The load imports
// the real `throwBoundaryError` from `$lib/api/loadError` (NOT the barrel) and the
// real `ApiError` CLASS from `$lib/api/errors`, so mocking the barrel down to just
// `{ listTickets, getRuns }` still leaves the boundary policy + `instanceof
// ApiError` intact.
vi.mock('$lib/api', () => ({ listTickets: vi.fn(), getRuns: vi.fn() }));

import { getRuns, listTickets } from '$lib/api';
import { ApiError } from '$lib/api/errors';
import type { ArtifactRead, RunRecord, TicketSummary } from '$lib/api';
import type { PageData } from './$types';
import { load, type RunRow } from './+page';
import Page from './+page.svelte';

const listTicketsMock = vi.mocked(listTickets);
const getRunsMock = vi.mocked(getRuns);

// `+page.svelte`'s `PageData` merges the root layout's `project`, so the rendered
// `data` prop must carry it too (the page itself only reads `data.rows`).
const project = {
	rootPath: '/home/dev/factory-console',
	ticketsManifestPath: '/home/dev/factory-console/docs/planning/tickets.json',
	ticketsDir: '/home/dev/factory-console/docs/planning/tickets',
	roadmapPath: null,
	runStateDir: null,
	discoveredAt: '2026-07-22T00:00:00Z'
};

const RESULTS_DIR = '/home/dev/factory-console/.factory/results';
const RECEIPTS_DIR = '/home/dev/factory-console/.factory/receipts';
const PR_URL = 'https://github.com/example/factory-console/pull/186';

function ticket(id: string, runState: TicketSummary['runState']): TicketSummary {
	return {
		id,
		title: `Ticket ${id}`,
		status: 'todo',
		track: 'frontend',
		milestone: 'v2.1',
		runState,
		depCount: 0,
		dependentCount: 0
	};
}

/** A read that yielded no data, with the reason the server names for it. */
function skipped(path: string, reason: NonNullable<ArtifactRead['reason']>): ArtifactRead {
	return { path, data: null, reason };
}

function read(path: string, data: Record<string, unknown>): ArtifactRead {
	return { path, data, reason: null };
}

function record(id: string, result: ArtifactRead, receipt: ArtifactRead): RunRecord {
	return { ticketId: id, result, receipt };
}

/** Both artifacts `absent` — a ticket the factory has never run on this machine. */
function neverRan(id: string): RunRecord {
	return record(
		id,
		skipped(`${RESULTS_DIR}/${id}.json`, 'absent'),
		skipped(`${RECEIPTS_DIR}/${id}.json`, 'absent')
	);
}

/** A finished lane: a result with a PR url and a status, beside a clean receipt. */
function finished(id: string, extra: Record<string, unknown> = {}): RunRecord {
	return record(
		id,
		read(`${RESULTS_DIR}/${id}.json`, {
			ticket_id: id,
			status: 'ready',
			pr_url: PR_URL,
			...extra
		}),
		read(`${RECEIPTS_DIR}/${id}.json`, { ticket_id: id, verdict: 'approved' })
	);
}

function rowsOf(records: RunRecord[], runState: TicketSummary['runState'] = 'ready'): RunRow[] {
	return records.map((r) => ({
		ticketId: r.ticketId,
		title: `Ticket ${r.ticketId}`,
		runState,
		record: r
	}));
}

function data(rows: RunRow[]): PageData {
	return { project, rows };
}

// Svelte's markup wraps long prose across lines; collapse whitespace before
// matching so an assertion is about the sentence, not about its indentation.
function normalized(node: Element | null | undefined): string {
	return (node?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

describe('runs load', () => {
	beforeEach(() => {
		listTicketsMock.mockReset();
		getRunsMock.mockReset();
	});

	it('joins the two endpoints by ticket id, in manifest order', async () => {
		listTicketsMock.mockResolvedValue([ticket('T88', 'merged'), ticket('T89', 'todo')]);
		getRunsMock.mockResolvedValue([neverRan('T89'), finished('T88')]);

		const result = await load({} as never);

		expect(listTicketsMock).toHaveBeenCalledTimes(1);
		expect(getRunsMock).toHaveBeenCalledTimes(1);
		// The manifest — i.e. the ticket list — is the list AND its order, so the runs
		// response's own ordering does not leak into the table.
		expect(result).toEqual({
			rows: [
				{ ticketId: 'T88', title: 'Ticket T88', runState: 'merged', record: finished('T88') },
				{ ticketId: 'T89', title: 'Ticket T89', runState: 'todo', record: neverRan('T89') }
			]
		});
	});

	it('carries a null record for a manifest ticket the runs listing does not name', async () => {
		listTicketsMock.mockResolvedValue([ticket('T88', 'todo')]);
		getRunsMock.mockResolvedValue([]);

		const result = await load({} as never);

		// NOT synthesised as `absent`: that would state, as a fact about the factory,
		// something that is actually a fact about the two reads disagreeing.
		expect(result).toEqual({
			rows: [{ ticketId: 'T88', title: 'Ticket T88', runState: 'todo', record: null }]
		});
	});

	it('drops a run record whose ticket is not in the manifest', async () => {
		listTicketsMock.mockResolvedValue([ticket('T88', 'todo')]);
		getRunsMock.mockResolvedValue([finished('T88'), finished('T99')]);

		const result = await load({} as never);

		expect(result).toEqual({
			rows: [{ ticketId: 'T88', title: 'Ticket T88', runState: 'todo', record: finished('T88') }]
		});
	});

	it('passes the all-absent response straight through rather than treating it as an error', async () => {
		listTicketsMock.mockResolvedValue([ticket('T88', 'todo')]);
		getRunsMock.mockResolvedValue([neverRan('T88')]);

		const result = await load({} as never);

		expect(result).toEqual({
			rows: [{ ticketId: 'T88', title: 'Ticket T88', runState: 'todo', record: neverRan('T88') }]
		});
	});

	it('converts an ApiError to a thrown SvelteKit boundary error preserving status + code', async () => {
		listTicketsMock.mockResolvedValue([]);
		getRunsMock.mockRejectedValue(
			new ApiError({ code: 'internal_error', message: 'Boom.', status: 500 })
		);

		await expect(load({} as never)).rejects.toMatchObject({
			status: 500,
			body: { code: 'internal_error', message: 'Boom.' }
		});
	});
});

describe('runs page', () => {
	it('renders the run state, PR link and outcome for a full record', () => {
		render(Page, { props: { data: data(rowsOf([finished('T88')], 'merged')) } });

		expect(screen.getByRole('heading', { level: 1, name: 'Runs' })).toBeTruthy();
		expect(screen.getByTestId('run-row-T88')).toBeTruthy();
		// The badge, from the JOINED ticket's run-state (the runs endpoint has none).
		expect(normalized(screen.getByTestId('run-row-T88'))).toMatch(/Merged/);

		const link = screen.getByTestId('pr-link-T88');
		expect(link.getAttribute('href')).toBe(PR_URL);
		expect(link.tagName).toBe('A');

		// The lane outcome verbatim as the factory wrote it.
		expect(normalized(screen.getByTestId('outcome-T88'))).toBe('ready');
		expect(normalized(screen.getByTestId('receipt-T88'))).toBe('Present');
	});

	it('renders the no-run-data explanation, NOT an empty table, when every record is absent', () => {
		// `unknown` on every row: no run-state source EITHER, so the whole-page claim
		// that this project has no run data is actually true. The fresh-clone case.
		render(Page, { props: { data: data(rowsOf([neverRan('T88'), neverRan('T89')], 'unknown')) } });

		// The bug this asserts against is a table of rows with empty cells, which reads
		// as "the factory ran and recorded nothing" — so assert the explanatory content
		// is PRESENT, not merely that no rows rendered.
		const banner = screen.getByTestId('no-run-data');
		expect(normalized(banner)).toMatch(/No factory run data in this project\./);
		expect(normalized(banner)).toMatch(/machine-local and gitignored/i);
		// Naming where it looked, taken from the records' own artifact paths.
		expect(screen.getByText(RESULTS_DIR)).toBeTruthy();
		expect(screen.getByText(RECEIPTS_DIR)).toBeTruthy();

		expect(screen.queryByRole('table')).toBeNull();
		expect(screen.queryByTestId('run-row-T88')).toBeNull();
	});

	it('keeps the table when the artifacts are all absent but the run-state source is not', () => {
		// The run-state column is fed from the project's run-state source, NOT from
		// `.factory/results|receipts` — and one of those sources (`docs/planning/
		// .run-state`) is committed, so it survives the fresh clone the artifacts do
		// not. Claiming "no factory run data in this project" off the artifacts alone
		// would hide a full board of real badges behind a banner denying it exists.
		render(Page, { props: { data: data(rowsOf([neverRan('T88'), neverRan('T89')], 'merged')) } });

		expect(screen.queryByTestId('no-run-data')).toBeNull();
		expect(screen.getByRole('table')).toBeTruthy();
		expect(normalized(screen.getByTestId('run-row-T88'))).toMatch(/Merged/);
		// …and the absent artifacts are still named per source rather than left blank.
		expect(screen.getByTestId('receipt-T88').getAttribute('data-reason')).toBe('absent');
	});

	it('renders the table with no banner when only some tickets have artifacts', () => {
		render(Page, { props: { data: data(rowsOf([finished('T88'), neverRan('T89')])) } });

		// Ordinary partial progress: the missing artifacts are un-run tickets, and
		// nothing in the response makes them a source-level problem.
		expect(screen.queryByTestId('no-run-data')).toBeNull();
		expect(screen.getByRole('table')).toBeTruthy();
		expect(screen.getByTestId('run-row-T88')).toBeTruthy();
		expect(screen.getByTestId('run-row-T89')).toBeTruthy();
		// The un-run row still names its absence per source rather than going blank.
		expect(normalized(screen.getByTestId('receipt-T89'))).toBe('—');
		expect(screen.getByTestId('receipt-T89').getAttribute('title')).toMatch(/never wrote/i);
	});

	it('marks a degraded receipt read distinguishably from a plain absence', () => {
		const degraded = record(
			'T89',
			read(`${RESULTS_DIR}/T89.json`, { status: 'ready' }),
			skipped(`${RECEIPTS_DIR}/T89.json`, 'unreadable')
		);
		render(Page, { props: { data: data(rowsOf([degraded, neverRan('T90')])) } });

		const bad = screen.getByTestId('receipt-T89');
		const absent = screen.getByTestId('receipt-T90');
		// A real failed read of something that IS there, versus a file that was never
		// written: these must not render alike, in text OR in the DOM.
		expect(normalized(bad)).toBe('Unreadable');
		expect(bad.getAttribute('data-degraded')).toBe('true');
		expect(normalized(absent)).toBe('—');
		expect(absent.getAttribute('data-degraded')).toBeNull();
	});

	// Every member of `ArtifactSkipReason`, so a transposed label or title cannot ship
	// green. The component's `Record<ArtifactSkipReason, …>` maps make a NEW member a
	// build error; this makes a WRONG member a test failure.
	it.each([
		['absent', '—', false],
		['unreadable', 'Unreadable', true],
		['unparseable', 'Unparseable', true],
		['too_large', 'Too large', true]
	] as const)('labels the %s reason as "%s"', (reason, label, degraded) => {
		const row = record(
			'T88',
			read(`${RESULTS_DIR}/T88.json`, { status: 'ready' }),
			skipped(`${RECEIPTS_DIR}/T88.json`, reason)
		);
		render(Page, { props: { data: data(rowsOf([row])) } });

		const cell = screen.getByTestId('receipt-T88');
		expect(normalized(cell)).toBe(label);
		expect(cell.getAttribute('data-reason')).toBe(reason);
		expect(cell.getAttribute('title')).toBeTruthy();
		expect(cell.getAttribute('data-degraded')).toBe(degraded ? 'true' : null);
	});

	it('distinguishes an absent artifact from one that was read and says nothing', () => {
		// Both render the same `—` glyph, so the DOM is the only thing that can carry
		// the difference — and they are different facts: "the factory never wrote this"
		// versus "the factory wrote it and named no value here". A `title` alone is
		// invisible to a screen reader and to anyone not hovering.
		const readEmpty = record(
			'T88',
			read(`${RESULTS_DIR}/T88.json`, {}),
			read(`${RECEIPTS_DIR}/T88.json`, { verdict: 'approved' })
		);
		render(Page, { props: { data: data(rowsOf([readEmpty, neverRan('T89')])) } });

		const wasRead = screen.getByTestId('outcome-T88');
		const neverWritten = screen.getByTestId('outcome-T89');
		expect(normalized(wasRead)).toBe('—');
		expect(normalized(neverWritten)).toBe('—');
		// Identical text, so this attribute is what makes them tellable apart.
		expect(wasRead.getAttribute('data-reason')).toBeNull();
		expect(neverWritten.getAttribute('data-reason')).toBe('absent');
	});

	it('marks the PR and outcome cells degraded when the RESULT itself could not be parsed', () => {
		const corrupt = record(
			'T89',
			skipped(`${RESULTS_DIR}/T89.json`, 'unparseable'),
			read(`${RECEIPTS_DIR}/T89.json`, { verdict: 'approved' })
		);
		render(Page, { props: { data: data(rowsOf([corrupt])) } });

		expect(screen.queryByTestId('pr-link-T89')).toBeNull();
		expect(normalized(screen.getByTestId('pr-T89'))).toBe('Unparseable');
		expect(screen.getByTestId('pr-T89').getAttribute('data-degraded')).toBe('true');
		expect(normalized(screen.getByTestId('outcome-T89'))).toBe('Unparseable');
		// The other source keeps its own answer: neither reason is inferred from the other.
		expect(normalized(screen.getByTestId('receipt-T89'))).toBe('Present');
	});

	it('renders no link element at all when the result names no pr_url', () => {
		const noPr = record(
			'T88',
			read(`${RESULTS_DIR}/T88.json`, { status: 'ready' }),
			read(`${RECEIPTS_DIR}/T88.json`, { verdict: 'approved' })
		);
		render(Page, { props: { data: data(rowsOf([noPr])) } });

		// A dead link is worse than no link: assert the element is absent, not just
		// that its href is empty.
		expect(screen.queryByTestId('pr-link-T88')).toBeNull();
		expect(screen.getByTestId('pr-T88').tagName).toBe('SPAN');
		expect(normalized(screen.getByTestId('pr-T88'))).toBe('—');
		// The result WAS read, so this is not a degraded cell.
		expect(screen.getByTestId('pr-T88').getAttribute('data-degraded')).toBeNull();
		expect(normalized(screen.getByTestId('outcome-T88'))).toBe('ready');
	});

	// `data` is whatever another process wrote into a local JSON file, so every one of
	// these is untrusted input that must not reach an `href`. Each is `pr_url`
	// RECORDED-BUT-UNUSABLE, which is a different fact from `pr_url` absent — so the
	// cell must not fall through to the "names no PR url under any key this console
	// recognises" wording that the names-no-pr_url case above owns.
	it.each<[string, unknown]>([
		// Handed straight to the browser as a clickable link if the scheme is not checked.
		['a javascript: scheme', 'javascript:alert(1)'],
		['a data: scheme', 'data:text/html,<script>alert(1)</script>'],
		// Reaches the `catch`: `new URL` with no base throws on a relative reference.
		['a relative reference', 'example/pull/1'],
		['unparseable junk', 'not a url at all'],
		// Reads as github.com but resolves to evil.test — the link's text is the fixed
		// words "Pull request", so the operator never sees where it actually goes.
		['a deceptive userinfo host', 'https://github.com%2Fexample%2Fpull%2F1@evil.test/'],
		// Would send these credentials to the host on click.
		['embedded credentials', 'https://user:pass@example.test/pull/1'],
		// Present, but not a string at all.
		['a non-string value', 42],
		['an all-whitespace string', '   ']
	])('refuses to link %s, and says the url is unusable rather than missing', (_label, prUrl) => {
		render(Page, { props: { data: data(rowsOf([finished('T88', { pr_url: prUrl })])) } });

		expect(screen.queryByTestId('pr-link-T88')).toBeNull();
		const cell = screen.getByTestId('pr-T88');
		expect(normalized(cell)).toBe('Unusable PR url');
		expect(cell.getAttribute('data-degraded')).toBe('true');
		expect(cell.getAttribute('title')).toMatch(/will not link/i);
		// `data-reason` is the ARTIFACT-level vocabulary (`ArtifactSkipReason`). This is
		// a FIELD-level fact about an artifact that read fine, so it must not borrow it —
		// pinned here so the two vocabularies cannot quietly merge.
		expect(cell.getAttribute('data-reason')).toBeNull();
	});

	it('links a normalized http(s) url and exposes its destination', () => {
		render(Page, { props: { data: data(rowsOf([finished('T88', { pr_url: PR_URL })])) } });

		const link = screen.getByTestId('pr-link-T88');
		// The href is the string that was VALIDATED (`URL.href`), not the raw input.
		expect(link.getAttribute('href')).toBe(new URL(PR_URL).href);
		// The link text is fixed prose, so the destination has to be visible somewhere.
		expect(link.getAttribute('title')).toBe(PR_URL);
	});

	// The same unusable-field classes the PR column above is swept over, so the two
	// columns cannot drift apart on what `readField` calls usable.
	it.each<[string, unknown]>([
		['a non-string value', 42],
		['an all-whitespace string', '   ']
	])('says the status is unusable, not missing, when it is %s', (_label, status) => {
		render(Page, { props: { data: data(rowsOf([finished('T88', { status })])) } });

		// The factory DID record a status. Rendering the same `—` as a result that
		// recorded none would report a value that exists as one that does not — and
		// would leave this column disagreeing with the PR column beside it, which
		// makes the same three-way distinction over the same untyped object.
		const cell = screen.getByTestId('outcome-T88');
		expect(normalized(cell)).toBe('Unusable status');
		expect(cell.getAttribute('data-degraded')).toBe('true');
		expect(cell.getAttribute('title')).toMatch(/not as a string/i);
		// Field-level, so it does not wear the artifact-level `data-reason`.
		expect(cell.getAttribute('data-reason')).toBeNull();
	});

	it('says the status is missing when the result names none at all', () => {
		const noStatus = record(
			'T88',
			read(`${RESULTS_DIR}/T88.json`, { pr_url: PR_URL }),
			read(`${RECEIPTS_DIR}/T88.json`, { verdict: 'approved' })
		);
		render(Page, { props: { data: data(rowsOf([noStatus])) } });

		const cell = screen.getByTestId('outcome-T88');
		expect(normalized(cell)).toBe('—');
		expect(cell.getAttribute('data-degraded')).toBeNull();
		// The claim is BOUNDED to the keys this console knows to look under. The key
		// names are a guess (`tests/fixtures/runs/README.md` disclaims them), so an
		// unqualified "names no status" would report the console's own vocabulary gap
		// as a fact about the artifact — the absent-vs-unread collapse, one level down.
		expect(cell.getAttribute('title')).toMatch(/names no status/i);
		expect(cell.getAttribute('title')).toMatch(/this console recognises/i);
	});

	it('says the manifest is empty rather than claiming there is no run data', () => {
		render(Page, { props: { data: data([]) } });

		// `[].every(…)` is true, so an empty manifest would otherwise render the
		// no-run-data explanation — a different fact entirely.
		expect(screen.queryByTestId('no-run-data')).toBeNull();
		expect(normalized(screen.getByTestId('empty-manifest'))).toMatch(/No tickets/i);
	});

	it('names a manifest ticket with no run record rather than rendering it as absent', () => {
		const rows: RunRow[] = [
			{ ticketId: 'T88', title: 'Ticket T88', runState: 'todo', record: null }
		];
		render(Page, { props: { data: data(rows) } });

		const marker = screen.getByTestId('no-record-T88');
		expect(normalized(marker)).toBe('No run record');
		expect(marker.getAttribute('data-degraded')).toBe('true');
		expect(screen.queryByTestId('receipt-T88')).toBeNull();
	});
});
