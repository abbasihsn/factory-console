import { render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Fully mock the API barrel so `load` never touches the network. The load imports
// the real `throwBoundaryError` from `$lib/api/loadError` (NOT the barrel) and the
// real `ApiError` CLASS from `$lib/api/errors`, so mocking the barrel down to just
// `{ getSpend }` still leaves the boundary policy + `instanceof ApiError` intact.
vi.mock('$lib/api', () => ({ getSpend: vi.fn() }));

import { getSpend } from '$lib/api';
import { ApiError } from '$lib/api/errors';
import type { SpendResponse } from '$lib/api';
import type { PageData } from './$types';
import { load } from './+page';
import Page from './+page.svelte';

const getSpendMock = vi.mocked(getSpend);

// `+page.svelte`'s `PageData` merges the root layout's data — the `project` plus
// the switcher's registry rows — so the rendered `data` prop must carry all of it
// (the page itself only reads the spend fields).
const project = {
	rootPath: '/home/dev/factory-console',
	ticketsManifestPath: '/home/dev/factory-console/docs/planning/tickets.json',
	ticketsDir: '/home/dev/factory-console/docs/planning/tickets',
	roadmapPath: '/home/dev/factory-console/ROADMAP.md',
	runStateDir: null,
	discoveredAt: '2026-07-22T00:00:00Z'
};

const LEDGER_PATH = '/home/dev/factory-console/.factory/ledger.jsonl';

function tokens(input: number, output: number, cacheRead: number, cacheCreation: number) {
	return {
		input,
		output,
		cacheRead,
		cacheCreation,
		total: input + output + cacheRead + cacheCreation
	};
}

// A full response: two ticket rows, three distinct model ids (one of them a model
// the console has never heard of), two levels.
const fullSpend = {
	attribution: 'full-to-each-id',
	totals: { costUsd: 1.4, entries: 3, tokens: tokens(12345, 6789, 1000000, 2000) },
	byTicket: [
		{ ticketId: 'T84', attributedCostUsd: 1.2, entries: 2, models: ['claude-opus-4-8[1m]'] },
		{ ticketId: 'T82', attributedCostUsd: 0.2, entries: 1, models: ['claude-haiku-4-5'] }
	],
	byModel: [
		{ model: 'claude-opus-4-8[1m]', costUsd: 1.1, tokens: tokens(10000, 5000, 900000, 1500) },
		{ model: 'claude-haiku-4-5', costUsd: 0.2, tokens: tokens(2000, 1500, 90000, 400) },
		{ model: 'zeta-9-experimental-preview', costUsd: 0.1, tokens: tokens(345, 289, 10000, 100) }
	],
	byLevel: [
		{ level: 'ticket', costUsd: 1.0, entries: 2 },
		{ level: 'review', costUsd: 0.4, entries: 1 }
	],
	source: { found: true, read: true, path: LEDGER_PATH },
	skipped: [],
	skippedOmitted: 0
} satisfies SpendResponse;

const noLedgerSpend = {
	attribution: 'full-to-each-id',
	totals: { costUsd: 0, entries: 0, tokens: tokens(0, 0, 0, 0) },
	byTicket: [],
	byModel: [],
	byLevel: [],
	source: { found: false, read: false, path: LEDGER_PATH },
	skipped: [],
	skippedOmitted: 0
} satisfies SpendResponse;

function data(spend: SpendResponse): PageData {
	return { project, projects: [], selectedId: null, spend };
}

// Svelte's markup wraps long prose across lines; collapse whitespace before
// matching so an assertion is about the sentence, not about its indentation.
function normalized(node: Element | null | undefined): string {
	return (node?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

describe('spend load', () => {
	beforeEach(() => {
		getSpendMock.mockReset();
	});

	it('returns the spend body', async () => {
		getSpendMock.mockResolvedValue(fullSpend);

		const result = await load({} as never);

		expect(getSpendMock).toHaveBeenCalledTimes(1);
		expect(result).toEqual({ spend: fullSpend });
	});

	it('passes the no-ledger body straight through rather than treating it as an error', async () => {
		getSpendMock.mockResolvedValue(noLedgerSpend);

		await expect(load({} as never)).resolves.toEqual({ spend: noLedgerSpend });
	});

	it('converts an ApiError to a thrown SvelteKit boundary error preserving status + code', async () => {
		getSpendMock.mockRejectedValue(
			new ApiError({ code: 'internal_error', message: 'Boom.', status: 500 })
		);

		await expect(load({} as never)).rejects.toMatchObject({
			status: 500,
			body: { code: 'internal_error', message: 'Boom.' }
		});
	});
});

describe('spend page', () => {
	it('renders the headline total to cents and all three cuts', () => {
		render(Page, { props: { data: data(fullSpend) } });

		expect(screen.getByRole('heading', { level: 1, name: 'Spend' })).toBeTruthy();
		expect(screen.getByTestId('spend-total').textContent?.trim()).toBe('$1.40');

		expect(screen.getByRole('heading', { level: 2, name: 'By ticket' })).toBeTruthy();
		expect(screen.getByRole('heading', { level: 2, name: 'By model' })).toBeTruthy();
		expect(screen.getByRole('heading', { level: 2, name: 'By level' })).toBeTruthy();

		// Per-ticket rows.
		expect(screen.getByText('T84')).toBeTruthy();
		expect(screen.getByText('$1.20')).toBeTruthy();
		expect(screen.getByText('T82')).toBeTruthy();

		// Per-level rows.
		expect(screen.getByText('ticket')).toBeTruthy();
		expect(screen.getByText('review')).toBeTruthy();
		expect(screen.getByText('$1.00')).toBeTruthy();
	});

	it('renders one row per model id in the fixture', () => {
		render(Page, { props: { data: data(fullSpend) } });

		for (const row of fullSpend.byModel) {
			// `getAllByText`: a model id also appears in the per-ticket table's
			// `models` column, so it is not unique on the page.
			expect(screen.getAllByText(row.model).length).toBeGreaterThan(0);
		}
	});

	it('renders an unrecognised model id verbatim rather than bucketing it', () => {
		render(Page, { props: { data: data(fullSpend) } });

		expect(screen.getByText('zeta-9-experimental-preview')).toBeTruthy();
		expect(screen.queryByText('other')).toBeNull();
		expect(screen.queryByText('Other')).toBeNull();
	});

	it('formats token counts with thousands separators', () => {
		render(Page, { props: { data: data(fullSpend) } });

		// The total row's summed tokens (12,345 + 6,789 + 1,000,000 + 2,000).
		expect(screen.getByText(/1,021,134/)).toBeTruthy();
	});

	it('renders the attribution rule beside the per-ticket table', () => {
		render(Page, { props: { data: data(fullSpend) } });

		const attribution = screen.getByTestId('attribution');
		expect(attribution.textContent?.trim()).toBe('full-to-each-id');
		// The explanation travels with it, so the over-summing column reads as
		// intended rather than as broken arithmetic.
		expect(normalized(attribution.parentElement)).toMatch(/sum to more than the total/i);
	});

	it('renders a per-ticket column that over-sums the total without any error path', () => {
		const overSumming = {
			...fullSpend,
			totals: { ...fullSpend.totals, costUsd: 1.4 },
			byTicket: [
				{ ticketId: 'T84', attributedCostUsd: 1.4, entries: 1, models: ['claude-opus-4-8[1m]'] },
				{ ticketId: 'T82', attributedCostUsd: 1.4, entries: 1, models: ['claude-opus-4-8[1m]'] }
			]
		} satisfies SpendResponse;

		render(Page, { props: { data: data(overSumming) } });

		expect(screen.getByTestId('spend-total').textContent?.trim()).toBe('$1.40');
		// Both rows render their full attributed cost — 2.80 attributed over a 1.40 total.
		expect(screen.getAllByText('$1.40')).toHaveLength(3);
		expect(screen.queryByTestId('partial-total')).toBeNull();
	});

	it('renders the no-ledger explanation and NO total figure when source.found is false', () => {
		const { container } = render(Page, { props: { data: data(noLedgerSpend) } });

		expect(screen.getByText(/No spend ledger for this project\./)).toBeTruthy();
		// The probed path, so the reader knows where the console looked.
		expect(screen.getByText(LEDGER_PATH)).toBeTruthy();
		expect(screen.getByText(/machine-local and gitignored/i)).toBeTruthy();

		// The bug this asserts against is rendering "$0.00" for an unmeasured
		// project, so assert the ABSENCE of any rendered money figure — not merely
		// that the page rendered.
		expect(screen.queryByTestId('spend-total')).toBeNull();
		expect(container.textContent).not.toMatch(/\$\s?\d/);
		expect(screen.queryByText('$0.00')).toBeNull();
		// And no zeroed tables either.
		expect(screen.queryByRole('table')).toBeNull();
		expect(screen.queryByRole('heading', { level: 2, name: 'By ticket' })).toBeNull();
	});

	it('renders the partial-total marker next to the total when lines were skipped', () => {
		const partial = {
			...fullSpend,
			skipped: [
				{ lineNo: 7, reason: 'not_json' as const },
				{ lineNo: 12, reason: 'invalid_entry' as const }
			],
			skippedOmitted: 3
		} satisfies SpendResponse;

		render(Page, { props: { data: data(partial) } });

		const marker = screen.getByTestId('partial-total');
		// 2 materialised skips + 3 omitted beyond the reader's detail cap.
		expect(normalized(marker)).toMatch(/5 ledger lines/);
		expect(normalized(marker)).toMatch(/excluded/i);
		// Adjacent to the figure, not a footnote elsewhere on the page.
		expect(marker.parentElement).toBe(screen.getByTestId('spend-total').parentElement);
	});

	it('does not render the partial-total marker when nothing was skipped', () => {
		render(Page, { props: { data: data(fullSpend) } });

		expect(screen.queryByTestId('partial-total')).toBeNull();
	});

	it('says the bill is unknown, and shows NO figure, when the ledger was found but never read', () => {
		const unread = {
			...fullSpend,
			totals: { costUsd: 0, entries: 0, tokens: tokens(0, 0, 0, 0) },
			byTicket: [],
			byModel: [],
			byLevel: [],
			source: { found: true, read: false, path: LEDGER_PATH },
			skipped: [{ lineNo: 0, reason: 'file_too_large' as const }],
			skippedOmitted: 0
		} satisfies SpendResponse;

		const { container } = render(Page, { props: { data: data(unread) } });

		expect(normalized(screen.getByTestId('unread-ledger'))).toMatch(/unknown/i);
		expect(screen.getByText(LEDGER_PATH)).toBeTruthy();

		// `totals` here is a placeholder for a bill nobody counted, so the same
		// assertion the no-ledger case makes applies: no money figure at all. A
		// "$0.00" under a caveat is still a number someone can screenshot.
		expect(screen.queryByTestId('spend-total')).toBeNull();
		expect(container.textContent).not.toMatch(/\$\s?\d/);
		// And no zeroed tables or "No X spend recorded." panels, which would assert
		// a measurement that never happened.
		expect(screen.queryByRole('table')).toBeNull();
		expect(screen.queryByText(/No ticket spend recorded/)).toBeNull();
		// It must NOT claim one countable line went missing: a line-0 skip stands
		// for the whole file.
		expect(container.textContent).not.toMatch(/1 ledger line/);
	});

	it('treats a found-but-unread ledger as unknown even when the body carries no skip entry', () => {
		// The line-0 skip is T82's convention, not a schema guarantee: `skipped` is
		// optional on the wire. Keying the unknown-bill case off it would let this
		// body render a confident "$0.00".
		const unreadNoSkip = {
			...fullSpend,
			totals: { costUsd: 0, entries: 0, tokens: tokens(0, 0, 0, 0) },
			byTicket: [],
			byModel: [],
			byLevel: [],
			source: { found: true, read: false, path: LEDGER_PATH },
			skipped: [],
			skippedOmitted: 0
		} satisfies SpendResponse;

		const { container } = render(Page, { props: { data: data(unreadNoSkip) } });

		expect(normalized(screen.getByTestId('unread-ledger'))).toMatch(/unknown/i);
		expect(screen.queryByTestId('spend-total')).toBeNull();
		expect(container.textContent).not.toMatch(/\$\s?\d/);
	});

	it('names the .factory/ directory in the unread case when the probed path is null', () => {
		// Mirrors the no-ledger branch: a sentence that says a ledger was found has
		// to be able to say where, so the location clause never just disappears.
		const unreadNoPath = {
			...fullSpend,
			totals: { costUsd: 0, entries: 0, tokens: tokens(0, 0, 0, 0) },
			byTicket: [],
			byModel: [],
			byLevel: [],
			source: { found: true, read: false, path: null },
			skipped: [{ lineNo: 0, reason: 'unreadable' as const }],
			skippedOmitted: 0
		} satisfies SpendResponse;

		render(Page, { props: { data: data(unreadNoPath) } });

		expect(normalized(screen.getByTestId('unread-ledger'))).toMatch(/unknown/i);
		expect(screen.getAllByText('.factory/').length).toBeGreaterThan(0);
	});

	it('marks the total partial when every skipped line fell past the reader detail cap', () => {
		// `skipped` is empty but lines ARE missing from the figure — gating the
		// notice on `skipped.length` would render this incomplete total as complete.
		const omittedOnly = {
			...fullSpend,
			skipped: [],
			skippedOmitted: 3
		} satisfies SpendResponse;

		render(Page, { props: { data: data(omittedOnly) } });

		const marker = screen.getByTestId('partial-total');
		expect(normalized(marker)).toMatch(/3 ledger lines/);
		expect(normalized(marker)).toMatch(/excluded/i);
	});

	it('renders a non-zero sub-cent cost as "<$0.01" rather than "$0.00"', () => {
		// Rounding a real-but-tiny bill to "$0.00" is the same false "this was free"
		// claim the no-ledger branch exists to prevent, reached by rounding.
		const subCent = {
			...fullSpend,
			totals: { ...fullSpend.totals, costUsd: 0.0031 },
			byTicket: [
				{ ticketId: 'T84', attributedCostUsd: 0.0031, entries: 1, models: ['claude-haiku-4-5'] }
			],
			byModel: [{ model: 'claude-haiku-4-5', costUsd: 0.0031, tokens: tokens(10, 5, 0, 0) }],
			byLevel: [{ level: 'ticket', costUsd: 0.0031, entries: 1 }]
		} satisfies SpendResponse;

		render(Page, { props: { data: data(subCent) } });

		expect(screen.getByTestId('spend-total').textContent?.trim()).toBe('<$0.01');
		// Every cut renders it the same way, so no row reads as free.
		expect(screen.getAllByText('<$0.01')).toHaveLength(4);
		expect(screen.queryByText('$0.00')).toBeNull();
	});

	it('renders a measured zero as "$0.00", not as the sub-cent marker', () => {
		const measuredZero = {
			...fullSpend,
			totals: { costUsd: 0, entries: 0, tokens: tokens(0, 0, 0, 0) },
			byTicket: [],
			byModel: [],
			byLevel: []
		} satisfies SpendResponse;

		render(Page, { props: { data: data(measuredZero) } });

		// `found: true, read: true` — the ledger WAS opened and it really did cost
		// nothing, so the zero is a measurement and is stated as one.
		expect(screen.getByTestId('spend-total').textContent?.trim()).toBe('$0.00');
		expect(screen.queryByText('<$0.01')).toBeNull();
	});

	it('renders the per-cut empty states when a read ledger has no rows', () => {
		const noRows = {
			...fullSpend,
			byTicket: [],
			byModel: [],
			byLevel: []
		} satisfies SpendResponse;

		render(Page, { props: { data: data(noRows) } });

		expect(screen.getByText('No ticket spend recorded.')).toBeTruthy();
		expect(screen.getByText('No model spend recorded.')).toBeTruthy();
		expect(screen.getByText('No level spend recorded.')).toBeTruthy();
		expect(screen.queryByRole('table')).toBeNull();
	});

	it('renders the empty states when the optional cut fields are omitted entirely', () => {
		// The server may omit these keys rather than send `[]` — they are optional in
		// the generated schema, which is what the page's `?? []` fallbacks are for.
		const omitted = {
			attribution: 'full-to-each-id',
			totals: fullSpend.totals,
			source: { found: true, read: true, path: LEDGER_PATH },
			skippedOmitted: 0
		} satisfies SpendResponse;

		render(Page, { props: { data: data(omitted) } });

		expect(screen.getByText('No ticket spend recorded.')).toBeTruthy();
		expect(screen.getByText('No model spend recorded.')).toBeTruthy();
		expect(screen.getByText('No level spend recorded.')).toBeTruthy();
		expect(screen.queryByTestId('partial-total')).toBeNull();
	});

	it('renders a ticket row whose models list is omitted', () => {
		const noModels = {
			...fullSpend,
			byTicket: [{ ticketId: 'T84', attributedCostUsd: 1.2, entries: 2 }]
		} satisfies SpendResponse;

		render(Page, { props: { data: data(noModels) } });

		expect(screen.getByText('T84')).toBeTruthy();
		expect(screen.getByText('$1.20')).toBeTruthy();
	});

	it('falls back to naming the .factory/ directory when the probed path is null', () => {
		const noPath = {
			...noLedgerSpend,
			source: { found: false, read: false, path: null }
		} satisfies SpendResponse;

		render(Page, { props: { data: data(noPath) } });

		expect(screen.getByText(/No spend ledger for this project\./)).toBeTruthy();
		expect(screen.queryByText(LEDGER_PATH)).toBeNull();
		expect(screen.getAllByText('.factory/').length).toBeGreaterThan(0);
	});

	it('uses singular wording for a single entry and a single skipped line', () => {
		const one = {
			...fullSpend,
			totals: { ...fullSpend.totals, entries: 1 },
			skipped: [{ lineNo: 7, reason: 'not_json' as const }],
			skippedOmitted: 0
		} satisfies SpendResponse;

		const { container } = render(Page, { props: { data: data(one) } });

		expect(normalized(screen.getByTestId('partial-total'))).toMatch(/1 ledger line could not be/);
		expect(container.textContent).toMatch(/1 ledger entry/);
	});
});
