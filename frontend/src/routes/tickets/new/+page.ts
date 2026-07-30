import type { PageLoad } from './$types';
import type { TicketFormValues } from '$lib/forms/ticketForm';

// No data load: the create form starts from a blank slate, so this route fetches
// nothing (SSR/prerender are already disabled at the root layout). It exists only to
// hand `+page.svelte` a stable set of empty initial values — `TicketForm` snapshots
// `initial` exactly once, so a fresh object per navigation keeps that seed clean.
export const load: PageLoad = (): { initial: TicketFormValues } => {
	return {
		initial: {
			id: '',
			title: '',
			dependsOn: '',
			provides: '',
			files: '',
			body: ''
		}
	};
};
