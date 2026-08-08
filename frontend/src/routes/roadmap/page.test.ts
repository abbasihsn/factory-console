import { render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Fully mock the API barrel so `load` never touches the network. The load imports
// the real `throwBoundaryError` from `$lib/api/loadError` (NOT the barrel) and the
// real `ApiError` CLASS from `$lib/api/errors`, so mocking the barrel down to just
// `{ getRoadmap }` still leaves the boundary policy + `instanceof ApiError` intact.
vi.mock('$lib/api', () => ({ getRoadmap: vi.fn() }));

import { getRoadmap } from '$lib/api';
import { ApiError } from '$lib/api/errors';
import type { Roadmap } from '$lib/api';
import type { PageData } from './$types';
import { load } from './+page';
import Page from './+page.svelte';

const getRoadmapMock = vi.mocked(getRoadmap);

// `+page.svelte`'s `PageData` merges the root layout's data — the `project` plus
// the switcher's registry rows — so the rendered `data` prop must carry all of it
// (the page itself only reads the roadmap fields).
const project = {
	rootPath: '/home/dev/factory-console',
	ticketsManifestPath: '/home/dev/factory-console/docs/planning/tickets.json',
	ticketsDir: '/home/dev/factory-console/docs/planning/tickets',
	roadmapPath: '/home/dev/factory-console/ROADMAP.md',
	runStateDir: null,
	discoveredAt: '2026-07-22T00:00:00Z'
};

const presentRoadmap = {
	path: '/home/dev/factory-console/ROADMAP.md',
	bodyMarkdown: 'The full rendered body.',
	bodyHtml: '<p>The full rendered body.</p>',
	milestones: [
		{
			name: 'MVP',
			items: [
				{ text: 'Wire the API client', ticketId: 'T31', runState: 'merged' },
				// Names no ticket: `runState` is absent, which the view renders as no badge.
				{ text: 'Draft the empty states' }
			]
		}
	]
} satisfies Extract<Roadmap, { path: string }>;

function emptyData(): PageData {
	return { project, projects: [], selectedId: null, present: false };
}

function presentData(): PageData {
	return { project, projects: [], selectedId: null, present: true, roadmap: presentRoadmap };
}

describe('roadmap load', () => {
	beforeEach(() => {
		getRoadmapMock.mockReset();
	});

	it('returns { present: false } when the roadmap is absent', async () => {
		getRoadmapMock.mockResolvedValue({ present: false });

		const result = await load({} as never);

		expect(getRoadmapMock).toHaveBeenCalledTimes(1);
		expect(result).toEqual({ present: false });
	});

	it('returns { present: true, roadmap } when a roadmap is present', async () => {
		getRoadmapMock.mockResolvedValue(presentRoadmap);

		const result = await load({} as never);

		expect(result).toEqual({ present: true, roadmap: presentRoadmap });
	});

	it('converts an ApiError to a thrown SvelteKit boundary error preserving status + code', async () => {
		getRoadmapMock.mockRejectedValue(
			new ApiError({ code: 'internal_error', message: 'Boom.', status: 500 })
		);

		await expect(load({} as never)).rejects.toMatchObject({
			status: 500,
			body: { code: 'internal_error', message: 'Boom.' }
		});
	});
});

describe('roadmap page', () => {
	it('renders the empty panel when there is no roadmap', () => {
		render(Page, { props: { data: emptyData() } });

		expect(screen.getByRole('heading', { level: 1, name: 'Roadmap' })).toBeTruthy();
		expect(screen.getByText('This project has no roadmap.')).toBeTruthy();
	});

	it('renders the milestone sections and the rendered body when present', () => {
		render(Page, { props: { data: presentData() } });

		expect(screen.getByRole('heading', { level: 1, name: 'Roadmap' })).toBeTruthy();
		expect(screen.queryByText('This project has no roadmap.')).toBeNull();
		// Milestone section content.
		expect(screen.getByRole('heading', { level: 2, name: 'MVP' })).toBeTruthy();
		expect(screen.getByText('Wire the API client')).toBeTruthy();
		expect(screen.getByRole('link', { name: 'T31' }).getAttribute('href')).toBe('/tickets/T31');
		// MarkdownBody renders the server-sanitized body HTML.
		expect(screen.getByText('The full rendered body.')).toBeTruthy();
	});
});
