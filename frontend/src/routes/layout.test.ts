import { render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// The load reads the registry through the API barrel, so it is mocked down to
// that one wrapper; the `/api/v1/project` read still goes through the injected
// `fetch` below. TopBar's `ProjectSwitcher` owns navigation, so `$app/navigation`
// is stubbed the way every other test that mounts an action-owning component does.
vi.mock('$lib/api', () => ({ listProjects: vi.fn() }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock('$app/state', () => ({ page: { url: new URL('http://localhost/') } }));

import { listProjects } from '$lib/api';
import type { RegisteredProjectOut } from '$lib/api';
import TopBar from '$lib/components/TopBar.svelte';
import { load } from './+layout';

const listProjectsMock = vi.mocked(listProjects);

function registryRow(id: string, selected: boolean): RegisteredProjectOut {
	return {
		id,
		name: id,
		path: `/home/dev/${id}`,
		addedAt: '2026-07-22T00:00:00Z',
		registered: true,
		selected,
		condition: 'ok'
	};
}

// The layout composes the presentational TopBar with `invalidateAll`; the smoke
// test targets TopBar with a supplied project prop so no router/backend is
// needed (see the ticket's testability note).
describe('TopBar (layout top bar)', () => {
	it('renders the app name and the supplied project root path', () => {
		const rootPath = '/home/dev/factory-console';
		render(TopBar, { props: { project: { rootPath } } });

		expect(screen.getByText('Factory Console')).toBeTruthy();

		const rootEl = screen.getByText(rootPath);
		expect(rootEl).toBeTruthy();
		// Full path is preserved in the title attribute (the text is truncated).
		expect(rootEl.getAttribute('title')).toBe(rootPath);
	});

	it('shows no switcher on a single project, and one once the registry has two', () => {
		const rootPath = '/home/dev/factory-console';
		const { container } = render(TopBar, { props: { project: { rootPath } } });
		expect(container.querySelector('select')).toBeNull();

		const switched = render(TopBar, {
			props: {
				project: { rootPath },
				projects: [registryRow('p1', true), registryRow('p2', false)],
				selectedId: 'p1'
			}
		});
		expect(switched.getByLabelText('Project')).toBeTruthy();
	});
});

// The load fetches /api/v1/project and maps every failure onto a normalized
// `ApiError` so `+error.svelte` can render it. Drive it with a mocked fetch so
// no router/backend is needed; `error()` throws an HttpError with `status`/`body`.
function jsonResponse(ok: boolean, status: number, body: unknown): Response {
	return { ok, status, json: async () => body } as unknown as Response;
}

describe('layout load', () => {
	beforeEach(() => {
		listProjectsMock.mockReset();
		listProjectsMock.mockResolvedValue([]);
	});

	it('maps a fetch rejection (backend unreachable) to a 503 network error', async () => {
		const fetch = vi.fn().mockRejectedValue(new Error('connection refused'));

		await expect(load({ fetch } as never)).rejects.toMatchObject({
			status: 503,
			body: { code: 'network_error' }
		});
	});

	it('propagates a non-OK response status and normalizes its error envelope', async () => {
		const fetch = vi
			.fn()
			.mockResolvedValue(
				jsonResponse(false, 404, { error: { code: 'project_not_found', message: 'No project.' } })
			);

		await expect(load({ fetch } as never)).rejects.toMatchObject({
			status: 404,
			body: { code: 'project_not_found', message: 'No project.' }
		});
	});

	it('returns the project on an OK response', async () => {
		const rootPath = '/home/dev/factory-console';
		const fetch = vi.fn().mockResolvedValue(jsonResponse(true, 200, { rootPath }));

		await expect(load({ fetch } as never)).resolves.toEqual({
			project: { rootPath },
			projects: [],
			selectedId: null
		});
	});

	it('returns the registry rows and derives the selected id from them', async () => {
		const rootPath = '/home/dev/factory-console';
		const fetch = vi.fn().mockResolvedValue(jsonResponse(true, 200, { rootPath }));
		const rows = [registryRow('p1', false), registryRow('p2', true)];
		listProjectsMock.mockResolvedValue(rows);

		await expect(load({ fetch } as never)).resolves.toEqual({
			project: { rootPath },
			projects: rows,
			selectedId: 'p2'
		});
	});

	it('reports no selected id when the registry names none', async () => {
		const rootPath = '/home/dev/factory-console';
		const fetch = vi.fn().mockResolvedValue(jsonResponse(true, 200, { rootPath }));
		listProjectsMock.mockResolvedValue([registryRow('p1', false)]);

		await expect(load({ fetch } as never)).resolves.toMatchObject({ selectedId: null });
	});

	it('degrades to an empty registry — not a blank shell — when the registry read fails', async () => {
		const rootPath = '/home/dev/factory-console';
		const fetch = vi.fn().mockResolvedValue(jsonResponse(true, 200, { rootPath }));
		listProjectsMock.mockRejectedValue(new Error('registry unreachable'));

		// The project still resolves, so the shell renders exactly as it did before
		// there was a registry at all — only the switcher is missing.
		await expect(load({ fetch } as never)).resolves.toEqual({
			project: { rootPath },
			projects: [],
			selectedId: null
		});
	});

	it('maps a 2xx body that is not valid JSON to a 503 invalid_response boundary', async () => {
		// A 200 whose body is not JSON must render the error boundary, not blank the
		// whole shell with a raw SyntaxError out of the load.
		const fetch = vi.fn().mockResolvedValue({
			ok: true,
			status: 200,
			json: async () => {
				throw new SyntaxError('not json');
			}
		} as unknown as Response);

		await expect(load({ fetch } as never)).rejects.toMatchObject({
			status: 503,
			body: { code: 'invalid_response' }
		});
	});
});
