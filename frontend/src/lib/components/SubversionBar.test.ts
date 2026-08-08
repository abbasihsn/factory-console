import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import SubversionBar from '$lib/components/SubversionBar.svelte';
import type { Subversion } from '$lib/api';

// SubversionBar is presentational: render it with a supplied record and assert what an
// operator can tell from the strip. The distinction it exists to draw is BUILDING vs
// WAITING — the factory records the branch when it cuts a sub-version and the PR url
// only once `ai-gh-open-pr` has run, so a record without a url is work in flight and one
// with a url is the gate the factory has stopped at.
const open: Subversion = {
	branch: 'factory/v1.0',
	baseSha: '0123456789abcdef',
	name: 'v1.0',
	prUrl: 'https://github.com/o/r/pull/7'
};

describe('SubversionBar', () => {
	it('names the sub-version and its branch when one is open', () => {
		render(SubversionBar, { props: { subversion: open } });

		expect(screen.getByText('v1.0')).toBeTruthy();
		expect(screen.getByText('factory/v1.0')).toBeTruthy();
	});

	it('links the PR when there is one, and opens it safely', () => {
		render(SubversionBar, { props: { subversion: open } });

		const link = screen.getByRole('link', { name: /Review the PR/ });
		expect(link.getAttribute('href')).toBe('https://github.com/o/r/pull/7');
		// The only link in the shell that leaves the console, and its target comes out of
		// the project's run-state file — a url this console does not own.
		expect(link.getAttribute('rel')).toContain('noopener');
		expect(link.getAttribute('rel')).toContain('noreferrer');
	});

	it('says the factory is WAITING once a PR exists', () => {
		render(SubversionBar, { props: { subversion: open } });

		expect(screen.getByText('Sub-version waiting to merge')).toBeTruthy();
	});

	it('says the factory is still BUILDING when no PR has been opened', () => {
		// Not a loading state and not a defect: the branch is cut and lanes are merging
		// into it. Nothing is being asked of a human yet, so the strip must not read as a
		// gate — an operator who cannot tell these apart either ignores a real hold or
		// goes looking for a PR that does not exist.
		render(SubversionBar, { props: { subversion: { ...open, prUrl: null } } });

		expect(screen.getByText('Sub-version in progress')).toBeTruthy();
		expect(screen.getByText('no PR opened yet')).toBeTruthy();
		expect(screen.queryByRole('link')).toBeNull();
	});

	it('paints the waiting state differently from the building one', () => {
		// The whole point of the strip is that a hold is NOTICED. Two states rendered in
		// the same colour would make the gate as easy to scroll past as the progress note.
		const waiting = render(SubversionBar, { props: { subversion: open } });
		const waitingClass = waiting.container.querySelector('div')?.className ?? '';
		waiting.unmount();

		const building = render(SubversionBar, { props: { subversion: { ...open, prUrl: null } } });
		const buildingClass = building.container.querySelector('div')?.className ?? '';

		expect(waitingClass).toContain('amber');
		expect(buildingClass).not.toContain('amber');
	});

	it('renders a record with only a branch', () => {
		// `name` and `baseSha` are optional on the wire. The branch alone still identifies
		// what is open, and dropping the strip for a partial record would hide a real hold.
		render(SubversionBar, { props: { subversion: { branch: 'factory/v2.0' } } });

		expect(screen.getByText('factory/v2.0')).toBeTruthy();
	});

	// Absent is the NORMAL state between cuts — the factory deletes the record when the
	// branch lands on main. Rendering nothing is what keeps the strip meaning "something
	// is open" whenever it is on screen at all.
	it.each([null, undefined])('renders nothing at all for %s', (subversion) => {
		const { container } = render(SubversionBar, { props: { subversion } });

		expect(container.querySelector('div')).toBeNull();
		expect(container.textContent?.trim()).toBe('');
	});
});
