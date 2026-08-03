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
		render(Page, { props: { data: data(rowsOf([neverRan('T88'), neverRan('T89')], 'todo')) } });

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

	it('renders no link for a pr_url that is not an http(s) url', () => {
		// `data` is whatever another process wrote into a local JSON file; a
		// `javascript:` href would be handed straight to the browser as a link.
		render(Page, {
			props: { data: data(rowsOf([finished('T88', { pr_url: 'javascript:alert(1)' })])) }
		});

		expect(screen.queryByTestId('pr-link-T88')).toBeNull();
		expect(normalized(screen.getByTestId('pr-T88'))).toBe('—');
	});

	it('renders no outcome value when the result carries a non-string status', () => {
		render(Page, { props: { data: data(rowsOf([finished('T88', { status: 42 })])) } });

		expect(normalized(screen.getByTestId('outcome-T88'))).toBe('—');
		expect(screen.getByTestId('outcome-T88').getAttribute('title')).toMatch(/names no status/i);
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
