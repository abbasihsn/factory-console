import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The form owns its one write, so `$lib/api` is mocked down to `addProject` —
// the same shape `ProjectSwitcher.test.ts` uses for `selectProject`. The
// component reads `normalizeError` from `$lib/api/contracts` and this file the
// `ApiError` CLASS from `$lib/api/errors` (neither via the barrel), so both stay
// real. No `$app/*` stub: this component navigates nowhere and reloads nothing —
// re-reading is the host page's job, reported through `onAdded`.
vi.mock('$lib/api', () => ({ addProject: vi.fn() }));

import { addProject } from '$lib/api';
import { ApiError } from '$lib/api/errors';
import type { RegisteredProjectOut } from '$lib/api';
import AddProjectForm from '$lib/components/AddProjectForm.svelte';
import { clearToken, setToken } from '$lib/stores/writeToken';

const addProjectMock = vi.mocked(addProject);

const TOKEN = 'test-write-token';

const CREATED: RegisteredProjectOut = {
	id: 'p9',
	name: 'minimal',
	path: '/home/dev/minimal',
	addedAt: '2026-07-22T00:00:00Z',
	registered: true,
	selected: false,
	condition: 'ok'
};

function pathField(): HTMLInputElement {
	return screen.getByLabelText('Project path') as HTMLInputElement;
}

function nameField(): HTMLInputElement {
	return screen.getByLabelText('Name (optional)') as HTMLInputElement;
}

// By role and type rather than by name: the label is the busy indicator
// ("Registering…"), so a name query would stop finding the button at exactly the
// moment the in-flight test needs to inspect it.
function registerButton(): HTMLButtonElement {
	return pathField().closest('form')!.querySelector('button[type="submit"]') as HTMLButtonElement;
}

async function fill(path: string, name?: string): Promise<void> {
	await fireEvent.input(pathField(), { target: { value: path } });
	if (name !== undefined) {
		await fireEvent.input(nameField(), { target: { value: name } });
	}
}

async function submit(): Promise<void> {
	await fireEvent.submit(pathField().closest('form')!);
}

describe('AddProjectForm', () => {
	beforeEach(() => {
		addProjectMock.mockReset();
		addProjectMock.mockResolvedValue(CREATED);
		setToken(TOKEN);
	});

	afterEach(() => {
		clearToken();
	});

	it('posts the trimmed path and the name with the held token', async () => {
		render(AddProjectForm, { props: {} });

		await fill('  /home/dev/minimal  ', 'minimal');
		await submit();

		await waitFor(() => expect(addProjectMock).toHaveBeenCalledTimes(1));
		expect(addProjectMock).toHaveBeenCalledWith(
			{ path: '/home/dev/minimal', name: 'minimal' },
			TOKEN
		);
	});

	it('omits a blank name rather than sending one, so the server labels the row', async () => {
		render(AddProjectForm, { props: {} });

		await fill('/home/dev/minimal', '   ');
		await submit();

		await waitFor(() => expect(addProjectMock).toHaveBeenCalledTimes(1));
		expect(addProjectMock).toHaveBeenCalledWith({ path: '/home/dev/minimal' }, TOKEN);
	});

	it('keeps submit inert while the path box is empty or whitespace', async () => {
		render(AddProjectForm, { props: {} });

		expect(registerButton().textContent?.trim()).toBe('Register');
		expect(registerButton().disabled).toBe(true);

		await fill('   ');
		expect(registerButton().disabled).toBe(true);
		// A stray Enter must not slip past the disabled button either.
		await submit();

		expect(addProjectMock).not.toHaveBeenCalled();
	});

	it('renders the server refusal by CODE and MESSAGE, never a message of its own', async () => {
		// The whole point of this form's failure state: which of the several ways a
		// path can be wrong fired. A regression to a generic message fails here.
		addProjectMock.mockRejectedValueOnce(
			new ApiError({
				code: 'invalid_project_path',
				message: '/home/dev/nope is not a directory.',
				status: 400
			})
		);
		render(AddProjectForm, { props: {} });

		await fill('/home/dev/nope');
		await submit();

		expect(await screen.findByText('invalid_project_path')).toBeTruthy();
		expect(screen.getByText('/home/dev/nope is not a directory.')).toBeTruthy();
		// The typed path survives the refusal — it is what a correction starts from.
		expect(pathField().value).toBe('/home/dev/nope');

		addProjectMock.mockResolvedValue(CREATED);
		await fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

		await waitFor(() => expect(addProjectMock).toHaveBeenCalledTimes(2));
	});

	it('asks for a token before writing when none is held, then resumes the submit', async () => {
		clearToken();
		render(AddProjectForm, { props: {} });

		await fill('/home/dev/minimal');
		await submit();

		const field = await screen.findByLabelText('Write token');
		expect(addProjectMock).not.toHaveBeenCalled();

		await fireEvent.input(field, { target: { value: 'fresh-token' } });
		await fireEvent.submit(field.closest('form')!);

		await waitFor(() => expect(addProjectMock).toHaveBeenCalledTimes(1));
		expect(addProjectMock).toHaveBeenCalledWith({ path: '/home/dev/minimal' }, 'fresh-token');
	});

	it('drops a rejected token, raises the prompt and resumes the same add on a 401', async () => {
		addProjectMock.mockRejectedValueOnce(
			new ApiError({ code: 'write_token_invalid', message: 'Invalid write token.', status: 401 })
		);
		render(AddProjectForm, { props: {} });

		await fill('/home/dev/minimal', 'minimal');
		await submit();

		const field = await screen.findByLabelText('Write token');
		expect(screen.getByRole('alert').textContent).toContain('rejected');
		expect(window.sessionStorage.getItem('factory-console:writeToken')).toBeNull();

		await fireEvent.input(field, { target: { value: 'fresh-token' } });
		await fireEvent.submit(field.closest('form')!);

		await waitFor(() => expect(addProjectMock).toHaveBeenCalledTimes(2));
		expect(addProjectMock).toHaveBeenLastCalledWith(
			{ path: '/home/dev/minimal', name: 'minimal' },
			'fresh-token'
		);
	});

	it('is inert while the add is in flight, so a slow probe cannot be double-submitted', async () => {
		let settle: (value: RegisteredProjectOut) => void = () => {};
		addProjectMock.mockReturnValue(
			new Promise<RegisteredProjectOut>((resolve) => {
				settle = resolve;
			})
		);
		render(AddProjectForm, { props: {} });

		await fill('/home/dev/minimal');
		await submit();

		await waitFor(() => expect(registerButton().disabled).toBe(true));
		// A submit on a disabled button cannot happen in a browser; firing one anyway
		// proves nothing beyond the flag would send a second POST.
		await submit();
		expect(addProjectMock).toHaveBeenCalledTimes(1);

		settle(CREATED);
		await waitFor(() => expect(pathField().value).toBe(''));
	});

	it('clears both fields and tells the host to re-read on success', async () => {
		const onAdded = vi.fn();
		render(AddProjectForm, { props: { onAdded } });

		await fill('/home/dev/minimal', 'minimal');
		await submit();

		await waitFor(() => expect(onAdded).toHaveBeenCalledTimes(1));
		expect(pathField().value).toBe('');
		expect(nameField().value).toBe('');
	});
});
