import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Fully mock the API barrel so neither `load` nor the two writes touch the
// network. The load imports the real `throwBoundaryError` from `$lib/api/loadError`
// (NOT the barrel) and the page imports the real `ApiError` CLASS from
// `$lib/api/errors`, so mocking the barrel down to these three still leaves the
// boundary policy and `instanceof ApiError` intact.
vi.mock('$lib/api', () => ({
	listProjects: vi.fn(),
	selectProject: vi.fn(),
	removeProject: vi.fn()
}));

// The page owns its two writes and the reload that follows each one, so
// `$app/navigation` is stubbed the same way `NavSearch.test.ts` and
// `ProjectSwitcher.test.ts` stub it.
vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));

import { invalidateAll } from '$app/navigation';
import { listProjects, removeProject, selectProject } from '$lib/api';
import { ApiError } from '$lib/api/errors';
import type { RegisteredProjectOut, RegistryEntryCondition } from '$lib/api';
import { clearToken, setToken } from '$lib/stores/writeToken';
import type { PageData } from './$types';
import { load } from './+page';
import Page from './+page.svelte';

const invalidateAllMock = vi.mocked(invalidateAll);
const listProjectsMock = vi.mocked(listProjects);
const removeProjectMock = vi.mocked(removeProject);
const selectProjectMock = vi.mocked(selectProject);

const TOKEN = 'test-write-token';

// `PageData` merges the root layout's data — the resolved project plus the
// switcher's own registry rows — so a rendered `data` prop must carry all of it
// even though this page reads only its own `projects`.
const project = {
	rootPath: '/home/dev/factory-console',
	ticketsManifestPath: '/home/dev/factory-console/docs/planning/tickets.json',
	ticketsDir: '/home/dev/factory-console/docs/planning/tickets',
	roadmapPath: '/home/dev/factory-console/ROADMAP.md',
	runStateDir: null,
	discoveredAt: '2026-07-22T00:00:00Z'
};

function row(
	id: string,
	name: string,
	selected: boolean,
	condition: RegistryEntryCondition = 'ok'
): RegisteredProjectOut {
	return {
		id,
		name,
		path: `/home/dev/${name}`,
		addedAt: '2026-07-22T00:00:00Z',
		registered: true,
		selected,
		condition
	};
}

const PROJECTS: RegisteredProjectOut[] = [row('p1', 'console', true), row('p2', 'factory', false)];

function data(projects: RegisteredProjectOut[]): PageData {
	return { project, projects, selectedId: projects.find((p) => p.selected)?.id ?? null };
}

describe('projects load', () => {
	beforeEach(() => {
		listProjectsMock.mockReset();
	});

	it('returns every registered row, degraded ones included', async () => {
		const rows = [...PROJECTS, row('p3', 'gone', false, 'path_missing')];
		listProjectsMock.mockResolvedValue(rows);

		const result = await load({} as never);

		expect(listProjectsMock).toHaveBeenCalledTimes(1);
		expect(result).toEqual({ projects: rows });
	});

	it('converts an ApiError to a thrown SvelteKit boundary error preserving status + code', async () => {
		listProjectsMock.mockRejectedValue(
			new ApiError({ code: 'internal_error', message: 'Boom.', status: 500 })
		);

		await expect(load({} as never)).rejects.toMatchObject({
			status: 500,
			body: { code: 'internal_error', message: 'Boom.' }
		});
	});
});

describe('projects page', () => {
	beforeEach(() => {
		invalidateAllMock.mockReset();
		removeProjectMock.mockReset();
		selectProjectMock.mockReset();
		removeProjectMock.mockResolvedValue(undefined);
		selectProjectMock.mockResolvedValue({ selected: row('p2', 'factory', true) });
		setToken(TOKEN);
	});

	afterEach(() => {
		clearToken();
	});

	it('lists every row with its name, path and added-at', () => {
		render(Page, { props: { data: data(PROJECTS) } });

		expect(screen.getByRole('heading', { level: 1, name: 'Projects' })).toBeTruthy();
		expect(screen.getByText('console')).toBeTruthy();
		expect(screen.getByText('factory')).toBeTruthy();

		// The path cell carries the full value in `title` too, so truncation never
		// hides the one field that tells two checkouts of a repo apart.
		const path = screen.getByText('/home/dev/factory');
		expect(path.getAttribute('title')).toBe('/home/dev/factory');
		expect(screen.getAllByText('2026-07-22T00:00:00Z')).toHaveLength(2);
	});

	it('marks the selected row and leaves its Select button inert', () => {
		render(Page, { props: { data: data(PROJECTS) } });

		expect(screen.getByTestId('project-row-p1').getAttribute('aria-current')).toBe('true');
		expect(screen.getByTestId('project-row-p2').getAttribute('aria-current')).toBeNull();

		expect((screen.getByRole('button', { name: 'Selected' }) as HTMLButtonElement).disabled).toBe(
			true
		);
		expect((screen.getByRole('button', { name: 'Select' }) as HTMLButtonElement).disabled).toBe(
			false
		);
	});

	it('names every condition the generated union carries', () => {
		const conditions: [RegistryEntryCondition, string][] = [
			['ok', 'OK'],
			['unreadable', 'Unreadable'],
			['path_missing', 'Path missing'],
			['not_a_project', 'Not a project'],
			['no_factory_dir', 'No .factory']
		];
		const rows = conditions.map(([condition], index) =>
			row(`p${index}`, `project-${index}`, false, condition)
		);
		render(Page, { props: { data: data(rows) } });

		for (const [, label] of conditions) {
			expect(screen.getByText(label)).toBeTruthy();
		}
	});

	it('names an unrecognised condition as itself rather than leaving the cell blank', () => {
		// Impossible per the generated type, which is the point: until the types are
		// regenerated a condition added server-side arrives as a value this build has
		// never heard of, and it must still be shown.
		const unknown = row('p9', 'future', false, 'quarantined' as RegistryEntryCondition);
		render(Page, { props: { data: data([unknown]) } });

		const cell = screen.getByText('quarantined');
		expect(cell.getAttribute('title')).toContain('does not recognise');
	});

	it('names the empty registry instead of rendering a blank table', () => {
		render(Page, { props: { data: data([]) } });

		expect(screen.getByTestId('empty-registry').textContent).toContain('No project is registered');
		expect(screen.queryByRole('table')).toBeNull();
	});

	it('writes the selection and then re-reads the page', async () => {
		render(Page, { props: { data: data(PROJECTS) } });

		await fireEvent.click(screen.getByRole('button', { name: 'Select' }));

		await waitFor(() => expect(invalidateAllMock).toHaveBeenCalledTimes(1));
		expect(selectProjectMock).toHaveBeenCalledWith('p2', TOKEN);
	});

	it('asks for confirmation before removing, and states that nothing on disk changes', async () => {
		render(Page, { props: { data: data(PROJECTS) } });

		await fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[1]);

		expect(screen.getByText('Remove project?')).toBeTruthy();
		const message = screen.getByRole('dialog').textContent ?? '';
		expect(message).toContain('Nothing on disk is touched');
		expect(message).toContain('/home/dev/factory');
		expect(removeProjectMock).not.toHaveBeenCalled();
	});

	it('removes nothing when the confirmation is cancelled', async () => {
		render(Page, { props: { data: data(PROJECTS) } });

		await fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[1]);
		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

		expect(screen.queryByText('Remove project?')).toBeNull();
		expect(removeProjectMock).not.toHaveBeenCalled();
		expect(invalidateAllMock).not.toHaveBeenCalled();
	});

	it('removes the row on confirmation and then re-reads the page', async () => {
		render(Page, { props: { data: data(PROJECTS) } });

		await fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[1]);
		await fireEvent.click(screen.getByRole('button', { name: 'Remove project' }));

		await waitFor(() => expect(invalidateAllMock).toHaveBeenCalledTimes(1));
		expect(removeProjectMock).toHaveBeenCalledWith('p2', TOKEN);
		// No optimistic patching: the row is still on screen until the re-run load
		// supplies a list without it.
		expect(screen.queryByText('Remove project?')).toBeNull();
	});

	it('asks for a token before opening the confirmation when none is held', async () => {
		clearToken();
		render(Page, { props: { data: data(PROJECTS) } });

		await fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[1]);

		expect(screen.queryByText('Remove project?')).toBeNull();
		const field = await screen.findByLabelText('Write token');
		expect(field).toBeTruthy();

		// Saving the token continues the removal — as far as the confirmation, which
		// still has to be answered.
		await fireEvent.input(field, { target: { value: 'fresh-token' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Save token' }));

		expect(screen.getByText('Remove project?')).toBeTruthy();
		expect(removeProjectMock).not.toHaveBeenCalled();
	});

	it('drops a rejected token and re-raises the prompt on a 401', async () => {
		removeProjectMock.mockRejectedValueOnce(
			new ApiError({ code: 'write_token_invalid', message: 'Invalid write token.', status: 401 })
		);
		render(Page, { props: { data: data(PROJECTS) } });

		await fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[1]);
		await fireEvent.click(screen.getByRole('button', { name: 'Remove project' }));

		const field = await screen.findByLabelText('Write token');
		expect(screen.getByRole('alert').textContent).toContain('rejected');
		expect(window.sessionStorage.getItem('factory-console:writeToken')).toBeNull();
		expect(invalidateAllMock).not.toHaveBeenCalled();

		await fireEvent.input(field, { target: { value: 'fresh-token' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Save token' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Remove project' }));

		await waitFor(() => expect(invalidateAllMock).toHaveBeenCalledTimes(1));
		expect(removeProjectMock).toHaveBeenLastCalledWith('p2', 'fresh-token');
	});

	it('reports any other failure inline without touching the list', async () => {
		selectProjectMock.mockRejectedValueOnce(
			new ApiError({ code: 'project_not_registered', message: 'No such project.', status: 404 })
		);
		render(Page, { props: { data: data(PROJECTS) } });

		await fireEvent.click(screen.getByRole('button', { name: 'Select' }));

		expect(await screen.findByText('No such project.')).toBeTruthy();
		expect(invalidateAllMock).not.toHaveBeenCalled();
		expect(screen.getByTestId('project-row-p2')).toBeTruthy();

		await fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
		expect(screen.queryByText('No such project.')).toBeNull();
	});
});
