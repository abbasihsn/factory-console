import type { RegistryEntryCondition } from '$lib/api';

/**
 * The one sentence naming what a degraded `RegistryEntryCondition` means, shared
 * between the `/projects` table (as a row's tooltip) and `ProjectStatusBanner`
 * (as its heading) so the two surfaces cannot drift on the same condition's
 * wording — each view still owns everything else itself: the table's pill
 * colour and short label, the banner's explanatory body and border tone.
 *
 * Keyed on every condition except `ok`, which neither view needs a sentence
 * for. EXHAUSTIVE by construction: a condition added server-side fails the
 * type-check here until it has one.
 */
export const CONDITION_TITLE: Record<Exclude<RegistryEntryCondition, 'ok'>, string> = {
	path_missing: 'This project’s registered path no longer exists.',
	not_a_project: 'This path is no longer a factory project.',
	no_factory_dir: 'This project has no .factory/ directory on this machine.',
	unreadable: 'This project’s path could not be read.'
};
