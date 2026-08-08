import type { PageLoad } from './$types';
import type { TicketFormValues } from '$lib/forms/ticketForm';

// No data load: the create form starts from a blank slate, so this route fetches
// nothing (SSR/prerender are already disabled at the root layout). It exists only to
// hand `+page.svelte` a stable set of empty initial values — `TicketForm` snapshots
// `initial` exactly once, so a fresh object per navigation keeps that seed clean.
//
// Every field is blank, INCLUDING the five App Factory v3 content fields, and none of
// them get a placeholder value. The five are required and a prefilled `N/A` or a stub
// heading would sail through both the form's validation and the server's — producing a
// ticket that satisfies the schema and tells a lane nothing. The empty string is the
// honest starting state: it is invalid, and the form says which field is missing and
// why it matters.
export const load: PageLoad = (): { initial: TicketFormValues } => {
	return {
		initial: {
			id: '',
			title: '',
			dependsOn: '',
			provides: '',
			context: '',
			approach: '',
			criticalFiles: '',
			interfaceData: '',
			verificationCommands: '',
			verificationNotes: ''
		}
	};
};
