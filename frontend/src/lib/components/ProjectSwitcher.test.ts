import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The switcher owns its navigation and its one write, so both are stubbed — the
// same shape `NavSearch.test.ts` uses for `goto`. `$lib/api` is mocked down to
// `selectProject`; the component imports the real `ApiError` CLASS from
// `$lib/api/errors` (not the barrel), so `instanceof` / `code` still work.
vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock('$lib/api', () => ({ selectProject: vi.fn() }));

// `page` is read only for its pathname, which decides between `goto` and a plain
// `invalidateAll`; a per-test URL is enough to drive both branches. Hoisted with
// the mock factory, which vitest lifts above the imports.
const pageState = vi.hoisted(() => ({ url: new URL('http://localhost/graph') }));
vi.mock('$app/state', () => ({ page: pageState }));

import { goto, invalidateAll } from '$app/navigation';
import { selectProject } from '$lib/api';
import { ApiError } from '$lib/api/errors';
import type { RegisteredProjectOut } from '$lib/api';
import ProjectSwitcher from '$lib/components/ProjectSwitcher.svelte';
import { clearToken, setToken } from '$lib/stores/writeToken';

const gotoMock = vi.mocked(goto);
const invalidateAllMock = vi.mocked(invalidateAll);
const selectProjectMock = vi.mocked(selectProject);

const TOKEN = 'test-write-token';

function row(id: string, name: string, selected: boolean): RegisteredProjectOut {
	return {
		id,
		name,
		path: `/home/dev/${name}`,
		addedAt: '2026-07-22T00:00:00Z',
		registered: true,
		selected,
		condition: 'ok'
	};
}

const PROJECTS: RegisteredProjectOut[] = [row('p1', 'console', true), row('p2', 'factory', false)];

function selection(id: string) {
	return { selected: row(id, id, true) };
}

describe('ProjectSwitcher', () => {
	beforeEach(() => {
		gotoMock.mockReset();
		invalidateAllMock.mockReset();
		selectProjectMock.mockReset();
		selectProjectMock.mockResolvedValue(selection('p2'));
		pageState.url = new URL('http://localhost/graph');
		setToken(TOKEN);
	});

	afterEach(() => {
		clearToken();
	});

	it('renders nothing when the registry is absent or names a single project', () => {
		const { container } = render(ProjectSwitcher, { props: { projects: [], selectedId: null } });
		expect(container.querySelector('select')).toBeNull();

		const one = render(ProjectSwitcher, {
			props: { projects: [PROJECTS[0]], selectedId: 'p1' }
		});
		expect(one.container.querySelector('select')).toBeNull();
	});

	it('lists every registered project, then the management entry, and shows the selected one', () => {
		render(ProjectSwitcher, { props: { projects: PROJECTS, selectedId: 'p1' } });

		const select = screen.getByLabelText('Project') as HTMLSelectElement;
		expect([...select.options].map((option) => option.value)).toEqual(['p1', 'p2', '__manage__']);
		expect([...select.options].map((option) => option.textContent?.trim())).toEqual([
			'console',
			'factory',
			'Manage projects…'
		]);
		expect(select.value).toBe('p1');
	});

	it('navigates to /projects from the management entry instead of switching to it', async () => {
		render(ProjectSwitcher, { props: { projects: PROJECTS, selectedId: 'p1' } });

		const select = screen.getByLabelText('Project') as HTMLSelectElement;
		await fireEvent.change(select, { target: { value: '__manage__' } });

		expect(gotoMock).toHaveBeenCalledWith('/projects');
		expect(selectProjectMock).not.toHaveBeenCalled();
		// The switcher survives the navigation, so the control must go back to the
		// project actually selected rather than sit on an entry that is not one.
		expect(select.value).toBe('p1');
	});

	it('writes the selection and then invalidates in place on a route with no ticket id', async () => {
		render(ProjectSwitcher, { props: { projects: PROJECTS, selectedId: 'p1' } });

		const select = screen.getByLabelText('Project');
		await fireEvent.change(select, { target: { value: 'p2' } });

		await waitFor(() => expect(invalidateAllMock).toHaveBeenCalledTimes(1));
		expect(selectProjectMock).toHaveBeenCalledWith('p2', TOKEN);
		expect(gotoMock).not.toHaveBeenCalled();
	});

	it('goes home from a route whose URL embeds a ticket id', async () => {
		pageState.url = new URL('http://localhost/tickets/T31/deps');
		render(ProjectSwitcher, { props: { projects: PROJECTS, selectedId: 'p1' } });

		await fireEvent.change(screen.getByLabelText('Project'), { target: { value: 'p2' } });

		await waitFor(() => expect(gotoMock).toHaveBeenCalledWith('/', { invalidateAll: true }));
		expect(invalidateAllMock).not.toHaveBeenCalled();
	});

	it('writes nothing when the current project is re-selected', async () => {
		render(ProjectSwitcher, { props: { projects: PROJECTS, selectedId: 'p1' } });

		await fireEvent.change(screen.getByLabelText('Project'), { target: { value: 'p1' } });

		expect(selectProjectMock).not.toHaveBeenCalled();
		expect(invalidateAllMock).not.toHaveBeenCalled();
	});

	it('is inert while a switch is in flight, so a second one cannot race it', async () => {
		type Selection = Awaited<ReturnType<typeof selectProject>>;
		let settle: (value: Selection) => void = () => {};
		selectProjectMock.mockReturnValue(
			new Promise<Selection>((resolve) => {
				settle = resolve;
			})
		);

		render(ProjectSwitcher, { props: { projects: PROJECTS, selectedId: 'p1' } });
		const select = screen.getByLabelText('Project') as HTMLSelectElement;
		await fireEvent.change(select, { target: { value: 'p2' } });

		await waitFor(() => expect(select.disabled).toBe(true));
		expect(select.getAttribute('aria-busy')).toBe('true');

		// A change on a disabled control cannot happen in a browser; firing one
		// anyway proves nothing beyond the flag would send a second write.
		await fireEvent.change(select, { target: { value: 'p1' } });
		expect(selectProjectMock).toHaveBeenCalledTimes(1);

		settle(selection('p2'));
		await waitFor(() => expect(select.disabled).toBe(false));
	});

	it('raises the write-token prompt on a 401 and resumes the switch once a token is pasted', async () => {
		selectProjectMock.mockRejectedValueOnce(
			new ApiError({ code: 'write_token_invalid', message: 'Invalid write token.', status: 401 })
		);
		render(ProjectSwitcher, { props: { projects: PROJECTS, selectedId: 'p1' } });

		await fireEvent.change(screen.getByLabelText('Project'), { target: { value: 'p2' } });

		// The rejected token is dropped, and the prompt says so rather than looking
		// like a switch that never had one.
		const field = await screen.findByLabelText('Write token');
		expect(screen.getByRole('alert').textContent).toContain('rejected');
		expect(window.sessionStorage.getItem('factory-console:writeToken')).toBeNull();

		selectProjectMock.mockResolvedValue(selection('p2'));
		await fireEvent.input(field, { target: { value: 'fresh-token' } });
		await fireEvent.submit(field.closest('form')!);

		await waitFor(() => expect(invalidateAllMock).toHaveBeenCalledTimes(1));
		expect(selectProjectMock).toHaveBeenLastCalledWith('p2', 'fresh-token');
	});

	it('reports any other failure inline with a Try again action', async () => {
		selectProjectMock.mockRejectedValueOnce(
			new ApiError({
				code: 'project_not_registered',
				message: 'No such project.',
				status: 404
			})
		);
		render(ProjectSwitcher, { props: { projects: PROJECTS, selectedId: 'p1' } });

		await fireEvent.change(screen.getByLabelText('Project'), { target: { value: 'p2' } });

		expect(await screen.findByText('No such project.')).toBeTruthy();
		expect(invalidateAllMock).not.toHaveBeenCalled();

		selectProjectMock.mockResolvedValue(selection('p2'));
		await fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

		await waitFor(() => expect(invalidateAllMock).toHaveBeenCalledTimes(1));
		expect(selectProjectMock).toHaveBeenCalledTimes(2);
	});

	it('asks for a token before writing when none is held', async () => {
		clearToken();
		render(ProjectSwitcher, { props: { projects: PROJECTS, selectedId: 'p1' } });

		await fireEvent.change(screen.getByLabelText('Project'), { target: { value: 'p2' } });

		expect(await screen.findByLabelText('Write token')).toBeTruthy();
		expect(selectProjectMock).not.toHaveBeenCalled();
	});
});
