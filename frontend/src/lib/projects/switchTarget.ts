/**
 * Where the console must land after the selected project changes.
 *
 * The switch itself is a server-side write, so every route has to re-read its
 * data afterwards; the only open question is whether the CURRENT URL still means
 * anything under the new project. For all but one shape of URL it does — `/`,
 * `/graph`, `/roadmap`, `/spend`, `/runs`, `/search?q=…` and `/tickets/new` name
 * a VIEW, not a record, so they describe the new project as well as they
 * described the old one and the user stays where they are.
 *
 * The exception is a URL that embeds a TICKET ID. A ticket id is a fact about one
 * project's manifest — `T31` in project A and `T31` in project B are unrelated
 * strings that happen to collide, and more often than not the id is simply absent
 * from the new manifest. Carrying it across a switch would therefore deep-link
 * into a ticket that is legitimately not there (or, worse, into a coincidentally
 * numbered ticket the user never asked for), so those routes go home to `/`.
 *
 * Only a SWITCH is redirected. A hand-typed (or bookmarked) deep link into a
 * ticket the current project does not have is deliberately left alone — the
 * detail loader's existing 404 → `notFound` panel already names that case, and it
 * names it better than a silent bounce to the index would.
 */

/**
 * `/tickets/<id>` and `/tickets/<id>/deps` — the two routes whose URL embeds a
 * ticket id. `<id>` is one path segment; an optional trailing slash is tolerated
 * because a URL is still the same route with one.
 *
 * `/tickets/new` is excluded explicitly: it is a STATIC route (SvelteKit resolves
 * it ahead of `/tickets/[id]`), so its segment is a verb, not an id, and it
 * describes the new project exactly as it described the old one.
 */
const TICKET_ID_ROUTE = /^\/tickets\/(?!new(?:\/|$))[^/]+(?:\/deps)?\/?$/;

/**
 * The path to navigate to after switching project from `pathname`, or `null` to
 * stay where we are and merely re-run the loads.
 *
 * Pure and router-free so the rule can be unit-tested per route: the caller
 * ({@link module:$lib/components/ProjectSwitcher}) supplies the current pathname
 * and performs the `goto` / `invalidateAll`.
 */
export function switchTarget(pathname: string): string | null {
	return TICKET_ID_ROUTE.test(pathname) ? '/' : null;
}
