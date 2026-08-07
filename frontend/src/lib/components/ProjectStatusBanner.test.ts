import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import type { RegisteredProjectOut } from '$lib/api';
import ProjectStatusBanner from '$lib/components/ProjectStatusBanner.svelte';

// A healthy row to vary one field of, so each case differs only by `condition`.
const OK_PROJECT: RegisteredProjectOut = {
	id: 'p1',
	name: 'factory-console',
	path: '/home/dev/factory-console',
	addedAt: '2026-01-01T00:00:00Z',
	registered: true,
	selected: true,
	condition: 'ok'
};

function withCondition(condition: RegisteredProjectOut['condition']): RegisteredProjectOut {
	return { ...OK_PROJECT, condition };
}

// Svelte's markup wraps long prose across lines; collapse whitespace before
// matching so an assertion is about the sentence, not about its indentation.
function normalized(node: Element | null | undefined): string {
	return (node?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

describe('ProjectStatusBanner', () => {
	it('names a moved or deleted registered path and its remedy', () => {
		render(ProjectStatusBanner, { props: { project: withCondition('path_missing') } });

		const banner = normalized(screen.getByTestId('project-condition'));
		expect(banner).toMatch(/registered path no longer exists/i);
		expect(banner).toMatch(/moved, renamed or deleted/i);
		expect(banner).toMatch(/re-point the registry entry/i);
	});

	it('names a path that is no longer a factory project', () => {
		render(ProjectStatusBanner, { props: { project: withCondition('not_a_project') } });

		const banner = normalized(screen.getByTestId('project-condition'));
		expect(banner).toMatch(/no longer a factory project/i);
		expect(banner).toMatch(/manifest is missing/i);
	});

	it('names a missing .factory/ directory and says it is not an error', () => {
		render(ProjectStatusBanner, { props: { project: withCondition('no_factory_dir') } });

		const banner = normalized(screen.getByTestId('project-condition'));
		expect(banner).toMatch(/no \.factory\/ directory on this machine/i);
		// The v3 clarification: a working copy that was never run here legitimately
		// has none, so this must not read as a failure.
		expect(banner).toMatch(/not an error/i);
		expect(banner).toMatch(/unknown on this machine rather than empty/i);
	});

	it('names an unreadable path as unread rather than empty', () => {
		render(ProjectStatusBanner, { props: { project: withCondition('unreadable') } });

		const banner = normalized(screen.getByTestId('project-condition'));
		expect(banner).toMatch(/could not be read/i);
		expect(banner).toMatch(/permissions problem or an I\/O error/i);
	});

	it('gives each degraded condition its own distinct text', () => {
		const texts = (['path_missing', 'not_a_project', 'no_factory_dir', 'unreadable'] as const).map(
			(condition) => {
				const { container, unmount } = render(ProjectStatusBanner, {
					props: { project: withCondition(condition) }
				});
				const text = normalized(container.querySelector('[data-testid="project-condition"]'));
				unmount();
				return text;
			}
		);

		expect(new Set(texts).size).toBe(texts.length);
	});

	it('names the project the condition is about', () => {
		render(ProjectStatusBanner, { props: { project: withCondition('path_missing') } });

		const banner = normalized(screen.getByTestId('project-condition'));
		expect(banner).toContain(OK_PROJECT.name);
		expect(banner).toContain(OK_PROJECT.path);
	});

	it('renders nothing at all for a healthy project', () => {
		const { container } = render(ProjectStatusBanner, { props: { project: OK_PROJECT } });

		expect(screen.queryByTestId('project-condition')).toBeNull();
		expect(container.textContent?.trim()).toBe('');
	});

	it('renders nothing at all when there is no registry entry', () => {
		// Single-project mode (no registry rows) must look exactly as it did before
		// this component existed.
		const { container } = render(ProjectStatusBanner, { props: { project: null } });

		expect(screen.queryByTestId('project-condition')).toBeNull();
		expect(container.textContent?.trim()).toBe('');
	});

	it('renders an unrecognised condition as itself rather than dropping it', () => {
		// Cast past the type system on purpose: a newer server can report a
		// condition this build has never heard of, and the resolution invariant says
		// what could not be understood is recorded, never swallowed.
		// (Through `unknown`, because the whole point is that the value does not
		// overlap the union the generated type declares.)
		const fromTheFuture = {
			...OK_PROJECT,
			condition: 'bogus'
		} as unknown as RegisteredProjectOut;
		render(ProjectStatusBanner, { props: { project: fromTheFuture } });

		const banner = normalized(screen.getByTestId('project-condition'));
		expect(banner).toMatch(/does not recognise this condition/i);
		expect(banner).toContain('bogus');
	});
});
