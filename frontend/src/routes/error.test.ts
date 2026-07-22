import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import ApiErrorView from '$lib/components/ApiErrorView.svelte';
import type { ApiError } from '$lib/api/contracts';

// `+error.svelte` reads `page.error`, normalizes it, and renders the
// presentational ApiErrorView; the smoke test targets ApiErrorView with a
// supplied error object so no router/backend is needed.
describe('ApiErrorView (error boundary)', () => {
	it('renders the supplied error code and message', () => {
		const error: ApiError = {
			code: 'project_not_found',
			message: 'No project could be discovered at the given path.'
		};
		render(ApiErrorView, { props: { error } });

		expect(screen.getByText(error.code)).toBeTruthy();
		expect(screen.getByText(error.message)).toBeTruthy();
	});

	it('renders the optional hint when present', () => {
		const hint = 'Is the backend running?';
		const error: ApiError = {
			code: 'network_error',
			message: 'Could not reach the backend.',
			hint
		};
		render(ApiErrorView, { props: { error } });

		expect(screen.getByText(hint)).toBeTruthy();
	});
});
