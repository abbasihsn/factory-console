import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { ApiError } from '$lib/api/contracts';
import ApiErrorView from '$lib/components/ApiErrorView.svelte';

const ERROR: ApiError = {
	code: 'write_gate_blocked',
	message: 'Ticket is not editable.',
	hint: 'Only todo tickets can be written.'
};

describe('ApiErrorView', () => {
	it("labels its button 'Reload' by default, so the error page keeps its wording", async () => {
		const onReload = vi.fn();
		render(ApiErrorView, { props: { error: ERROR, onReload } });

		const button = screen.getByRole('button', { name: 'Reload' });
		await fireEvent.click(button);

		expect(onReload).toHaveBeenCalledTimes(1);
	});

	it('uses a caller-supplied label for an action that is not a reload', () => {
		render(ApiErrorView, { props: { error: ERROR, onReload: vi.fn(), label: 'Close' } });

		expect(screen.getByRole('button', { name: 'Close' })).toBeTruthy();
		expect(screen.queryByRole('button', { name: 'Reload' })).toBeNull();
	});
});
