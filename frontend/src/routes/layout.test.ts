import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import TopBar from '$lib/components/TopBar.svelte';
import { load } from './+layout';

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
});

// The load fetches /api/v1/project and maps every failure onto a normalized
// `ApiError` so `+error.svelte` can render it. Drive it with a mocked fetch so
// no router/backend is needed; `error()` throws an HttpError with `status`/`body`.
function jsonResponse(ok: boolean, status: number, body: unknown): Response {
	return { ok, status, json: async () => body } as unknown as Response;
}

describe('layout load', () => {
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

		await expect(load({ fetch } as never)).resolves.toEqual({ project: { rootPath } });
	});
});
