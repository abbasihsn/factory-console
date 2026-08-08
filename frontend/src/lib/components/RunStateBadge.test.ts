import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { RunState } from '$lib/api';
import RunStateBadge from '$lib/components/RunStateBadge.svelte';

// RunStateBadge is presentational: render it with a supplied `runState` and
// snapshot the pill element per variant so the color map, humanized labels, and
// per-state title tooltip are pinned. The snapshot targets the `<span>` itself,
// not the container, so it carries no whitespace-only sibling text node (which
// the repo's trailing-whitespace hook would strip and desync). `runState` values
// are hyphenated (`in-flight`), matching the generated type.
describe('RunStateBadge', () => {
	it('renders the todo variant', () => {
		const { container } = render(RunStateBadge, { props: { runState: 'todo' } });

		expect(screen.getByText('To do')).toBeTruthy();
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-gray-100 text-gray-800"
			  title="Queued — no factory lane has started this ticket yet"
			>
			  To do
			</span>
		`);
	});

	it('renders the in-flight variant', () => {
		const { container } = render(RunStateBadge, { props: { runState: 'in-flight' } });

		expect(screen.getByText('In flight')).toBeTruthy();
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-amber-100 text-amber-800"
			  title="A factory lane is actively building this ticket"
			>
			  In flight
			</span>
		`);
	});

	it('renders the ready variant', () => {
		const { container } = render(RunStateBadge, { props: { runState: 'ready' } });

		expect(screen.getByText('Ready')).toBeTruthy();
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-green-100 text-green-800"
			  title="Built and reviewed — the PR is ready to merge"
			>
			  Ready
			</span>
		`);
	});

	it('renders the merged variant', () => {
		const { container } = render(RunStateBadge, { props: { runState: 'merged' } });

		expect(screen.getByText('Merged')).toBeTruthy();
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-violet-100 text-violet-800"
			  title="The ticket PR has been merged"
			>
			  Merged
			</span>
		`);
	});

	it('renders the unknown variant with an explanatory title tooltip', () => {
		const { container } = render(RunStateBadge, { props: { runState: 'unknown' } });

		const pill = screen.getByText('Unknown');
		// The tooltip must cover EVERY way the server answers `unknown` — no source,
		// a source that vanished, one that lists nobody, one whose content made no
		// sense — not just the first. It must NOT claim "could not be read": since
		// T80's second amendment that is a different state (`unreadable`) with a
		// different consequence, and saying it here would send an operator whose
		// run-state.json is merely corrupt hunting a permissions problem.
		expect(pill.getAttribute('title')).toBe(
			'No run-state source for this project, or its source could not be understood'
		);
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-slate-100 text-slate-500"
			  title="No run-state source for this project, or its source could not be understood"
			>
			  Unknown
			</span>
		`);
	});

	// T80: absent is DISTINCT from unknown — a run-state source WAS resolved and
	// simply does not list this ticket, unlike unknown's "no usable source to ask".
	it('renders the absent variant with an explanatory title tooltip', () => {
		const { container } = render(RunStateBadge, { props: { runState: 'absent' } });

		const pill = screen.getByText('Not listed');
		expect(pill.getAttribute('title')).toBe(
			'A run-state source exists but does not list this ticket'
		);
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-slate-200 text-slate-600"
			  title="A run-state source exists but does not list this ticket"
			>
			  Not listed
			</span>
		`);
	});

	// T80 amendment 2: `unreadable` is distinct from BOTH of its unnamed siblings —
	// the information is unavailable, so the console refuses every write to the ticket
	// until that is fixed. The pill is deliberately not slate: this is the only one of
	// the three an operator must act on.
	//
	// The tooltip used to end "(check its permissions)". Amendment 4 gave this state a
	// SECOND cause — a source read perfectly well that says something about this ticket
	// the console cannot interpret — and a badge has only the enum member, so it cannot
	// tell which. Naming permissions would be the wrong fix half the time; the server's
	// 409 is what carries the specific cause and names the offending value.
	it('renders the unreadable variant, distinctly from unknown and absent', () => {
		const { container } = render(RunStateBadge, { props: { runState: 'unreadable' } });

		const pill = screen.getByText('Unreadable');
		expect(pill.getAttribute('title')).toBe(
			'The run-state source could not be read, or says something about this ticket this console does not understand — writes are refused'
		);
		expect(pill.getAttribute('title')).not.toContain('permissions');
		expect(container.querySelector('span')).toMatchInlineSnapshot(`
			<span
			  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-red-50 text-red-800 ring-1 ring-red-300"
			  title="The run-state source could not be read, or says something about this ticket this console does not understand — writes are refused"
			>
			  Unreadable
			</span>
		`);

		const classes = (state: RunState) =>
			render(RunStateBadge, { props: { runState: state } }).container.querySelector('span')
				?.className;
		expect(classes('unreadable')).not.toBe(classes('unknown'));
		expect(classes('unreadable')).not.toBe(classes('absent'));
	});

	// EVERY member of the union, and the union is READ FROM THE GENERATED TYPE rather
	// than hand-listed here. `Record<RunState, string>` is what does it: a state added
	// to the backend enum and regenerated into `RunState` makes this object a missing
	// key — a compile error in `pnpm check` — so the new member cannot reach main with
	// no test, which a literal array of the states someone remembered would allow.
	// Iterating the same object then turns that compile-time guarantee into a rendered
	// assertion per state: a state the component's map forgot renders an empty span
	// with `undefined` classes, which is exactly what shipped for every factory state
	// before the JSON source was read at all.
	const EXPECTED_LABELS: Record<RunState, string> = {
		todo: 'To do',
		'in-flight': 'In flight',
		in_progress: 'In progress',
		in_part: 'In part',
		in_submilestone: 'In submilestone',
		ready: 'Ready',
		merged: 'Merged',
		flagged: 'Flagged',
		failed: 'Failed',
		needs_human: 'Needs human',
		unknown: 'Unknown',
		absent: 'Not listed',
		unreadable: 'Unreadable'
	};

	it.each(Object.entries(EXPECTED_LABELS) as [RunState, string][])(
		'renders the %s variant as a labelled, titled, styled pill',
		(runState, label) => {
			const { container } = render(RunStateBadge, { props: { runState } });

			const pill = container.querySelector('span');
			expect(pill?.textContent?.trim()).toBe(label);
			expect(pill?.getAttribute('title')).toBeTruthy();
			expect(pill?.className).not.toContain('undefined');
		}
	);

	// The three failure-ish states must be visually distinct from the in-progress
	// ones: an operator scanning the board needs "a lane stopped and something is
	// wrong" to look different from "a lane is working".
	it.each(['flagged', 'failed', 'needs_human'] as const)('paints %s as a failure pill', (state) => {
		const { container } = render(RunStateBadge, { props: { runState: state } });
		expect(container.querySelector('span')?.className).toContain('red');
	});

	it.each(['in_progress', 'in_part', 'in_submilestone'] as const)(
		'paints %s as an in-progress pill',
		(state) => {
			const { container } = render(RunStateBadge, { props: { runState: state } });
			const className = container.querySelector('span')?.className ?? '';
			expect(className).toContain('amber');
			expect(className).not.toContain('red');
		}
	);

	// The lane PHASE — where an in_progress lane has actually got to. A lane holds its
	// worktree for up to 90 minutes, and `in_progress` alone is a 90-minute black box in
	// the one place an operator most wants a reading.
	describe('the lane phase qualifier', () => {
		it('appends the phase to the label', () => {
			render(RunStateBadge, { props: { runState: 'in_progress', phase: 'reviewing' } });

			expect(screen.getByText('In progress · reviewing')).toBeTruthy();
		});

		it('explains the phase in the tooltip as well as the label', () => {
			const { container } = render(RunStateBadge, {
				props: { runState: 'in_progress', phase: 'verifying' }
			});

			expect(container.querySelector('span')?.getAttribute('title')).toContain(
				'currently verifying'
			);
		});

		// A phase the console has not heard of is DISPLAYED, not dropped and not escalated.
		// The server treats an unrecognised STATUS as `unreadable` and refuses every write;
		// a phase is gated on by nothing, so blanking it would only cost the operator a
		// reading because the factory added a word.
		it('shows a phase this console does not know rather than blanking it', () => {
			render(RunStateBadge, { props: { runState: 'in_progress', phase: 'auditing' } });

			expect(screen.getByText('In progress · auditing')).toBeTruthy();
		});

		it('renders exactly as before when there is no phase', () => {
			const withoutProp = render(RunStateBadge, { props: { runState: 'in_progress' } });
			const before = withoutProp.container.innerHTML;
			withoutProp.unmount();

			for (const phase of [null, undefined, '', '   ']) {
				const { container, unmount } = render(RunStateBadge, {
					props: { runState: 'in_progress', phase }
				});
				expect(container.innerHTML).toBe(before);
				unmount();
			}
		});

		// The state decides the colour; the phase never does. It qualifies a state, and a
		// pill that changed colour per phase would compete with the palette an operator
		// scans the board by.
		it('does not change the pill colour', () => {
			const plain = render(RunStateBadge, { props: { runState: 'in_progress' } });
			const plainClass = plain.container.querySelector('span')?.className;
			plain.unmount();

			const phased = render(RunStateBadge, {
				props: { runState: 'in_progress', phase: 'fixing' }
			});

			expect(phased.container.querySelector('span')?.className).toBe(plainClass);
		});
	});
});
