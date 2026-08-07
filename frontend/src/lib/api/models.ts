/**
 * Friendly names for the API domain types.
 *
 * Every type is aliased straight from the generated `./types` (`--immutable`, so
 * every field is `readonly`). Keeping the aliases here (rather than in `client.ts`)
 * gives `client.ts`, `index.ts`, and `contracts.ts` one shared source without any
 * circular import.
 */
import type { components } from './types';

export type Project = components['schemas']['Project'];
export type Ticket = components['schemas']['Ticket'];
export type TicketSummary = components['schemas']['TicketSummary'];
export type TicketListResponse = components['schemas']['TicketListResponse'];
export type RunState = components['schemas']['RunState'];

/** Liveness-probe body from `GET /api/v1/health` (`ok`, `version`, `projectRoot`). */
export type Health = components['schemas']['HealthResponse'];

export type TicketGraph = components['schemas']['TicketGraph'];
export type GraphNode = components['schemas']['GraphNode'];
export type GraphEdge = components['schemas']['GraphEdge'];

export type SearchResponse = components['schemas']['SearchResponse'];
export type SearchHit = components['schemas']['SearchHit'];

export type RoadmapMilestone = components['schemas']['RoadmapMilestone'];
export type RoadmapItem = components['schemas']['RoadmapItem'];

/**
 * `GET /api/v1/roadmap` returns the project's roadmap document, or an absence
 * marker when the project has no `ROADMAP.md`. This is an `anyOf` union: the
 * present branch (`Roadmap`) carries `path`/`bodyMarkdown`/`bodyHtml`/`milestones`
 * and NO `present` field, while the absent branch (`RoadmapAbsent`) sets
 * `present: false` — so consumers discriminate on the `present` key's presence.
 */
export type Roadmap = components['schemas']['Roadmap'] | components['schemas']['RoadmapAbsent'];

/** Dependency neighborhood of one ticket from `GET /api/v1/tickets/{id}/deps`. */
export type DepNeighborhood = components['schemas']['DepNeighborhood'];

/**
 * Request body for `POST /api/v1/tickets`. The server publishes this schema as
 * `TicketDraft`; `TicketCreate` is the friendly name the client uses.
 *
 * The SERVER requires only `id`, `title`, and `bodyMarkdown` — every other field
 * has a default. This TYPE additionally requires `provides`, which is a codegen
 * artifact, not a server rule: `provides` is the one defaulted field whose default
 * is a non-null literal (`""`), and `openapi-typescript` marks a property with a
 * default as non-optional. So a call site must pass `provides: ''` to type-check
 * even though omitting it over the wire is valid.
 */
export type TicketCreate = components['schemas']['TicketDraft'];

/**
 * Request body for `PUT /api/v1/tickets/{id}`. Published as `TicketEdit` —
 * identical to {@link TicketCreate} minus `id`, which comes from the path
 * (including the `provides` caveat noted there).
 *
 * One edit-only difference from create: `frontMatter` is an OVERLAY on the ticket
 * `.md`'s existing YAML header, not a replacement. Keys sent here are added or
 * overridden, keys already on disk are preserved, and there is no way to delete a
 * key. Factory-owned keys (`id`, `status`, and the fields mirrored from the
 * manifest entry) are ignored here — they follow the named fields above.
 */
export type TicketUpdate = components['schemas']['TicketEdit'];

/**
 * Uniform response body of EVERY write verb — create, update, and delete, on
 * both the applying and the dry-run path. An apply sets `applied: true` and
 * carries the re-read `ticket`; a dry-run sets `applied: false`, `ticket: null`,
 * and reports the paths/diff that WOULD be written.
 */
export type WriteResult = components['schemas']['WriteResult'];

/**
 * A dry-run preview.
 *
 * The server publishes NO separate preview schema: `?dryRun=true` returns the same
 * {@link WriteResult} envelope with `applied: false` and `ticket: null`. So this is
 * an alias, not a distinct shape — it exists to let a preview-rendering call site
 * name what it is holding without implying a second contract.
 */
export type WritePreview = WriteResult;

/** The per-file diffs one write produces, carried as a {@link WriteResult}'s `diff`. */
export type DiffPreview = components['schemas']['DiffPreview'];

/** A single file's planned or applied change as a unified diff, inside {@link DiffPreview}. */
export type FileDiff = components['schemas']['FileDiff'];

/**
 * `GET /api/v1/spend` — totals, the three cuts, and how the ledger was read.
 *
 * `source.found` (NOT a zero total) is what says whether the project has a ledger
 * at all, and `attribution` names the rule under which {@link TicketSpend} rows
 * may sum to more than `totals.costUsd`.
 */
export type SpendResponse = components['schemas']['SpendResponse'];

/** One ticket id's attributed spend, inside a {@link SpendResponse}'s `byTicket`. */
export type TicketSpend = components['schemas']['TicketSpend'];

/** One model id's project-wide share of the bill, inside `byModel`. */
export type ModelSpend = components['schemas']['ModelSpend'];

/** One agent level's share of the bill, inside `byLevel`. */
export type LevelSpend = components['schemas']['LevelSpend'];

/** `{ items, total }` envelope of `GET /api/v1/runs`, unwrapped by `getRuns`. */
export type RunListResponse = components['schemas']['RunListResponse'];

/**
 * One manifest ticket's two factory artifacts — `.factory/results/<id>.json` and
 * `.factory/receipts/<id>.json` — each as its own {@link ArtifactRead}.
 *
 * There is a record per MANIFEST ticket, including tickets the factory has never
 * run (both sources then say `absent`), so a record's presence says nothing about
 * whether a run happened; only the per-source `reason` does.
 *
 * Aliased from the server's `ProjectedRunRecord` — the wire twin of its internal
 * `RunRecord` — because the endpoint discloses a narrowed record, not its composed
 * domain one (see {@link ArtifactRead}). The friendly name stays `RunRecord`: the
 * projection is a server-side policy, and every consumer here reads the same
 * `ticketId`/`result`/`receipt` it always did.
 */
export type RunRecord = components['schemas']['ProjectedRunRecord'];

/**
 * One artifact read: `data` when it parsed, `reason` when it did not — exactly one
 * of the two, enforced server-side.
 *
 * `data` is what the server DISCLOSES of the artifact, not the artifact: a
 * `{ [key: string]: string }` holding only the keys in `DISCLOSED_ARTIFACT_FIELDS`
 * (`server/factory_console/api/v1/runs.py`) that the file carries as strings — the
 * same names the runs view declares in its `PROJECTED_FIELDS`. A read that names
 * none of them is `{}`, which is NOT `null`; `null` means the read failed and
 * `reason` says why.
 *
 * So the server still models no field inside a factory artifact — it has none it has
 * verified against a real captured file — it simply declines to forward the ones
 * nobody asked for. Read fields out of it defensively (a key may be missing) and
 * never widen this into a hand-written schema here; that would put back the
 * guesswork the backend refused to ship.
 */
export type ArtifactRead = components['schemas']['ProjectedArtifactRead'];

/**
 * Why an artifact yielded no data: `absent` (the factory never wrote it — the
 * ordinary state of a fresh clone) versus `unreadable`/`unparseable`/`too_large`,
 * which are real degraded reads and must not render like a plain absence.
 *
 * Derived from {@link ArtifactRead} rather than aliased from a schema, because the
 * server inlines this union into the field instead of publishing it as its own
 * component — so a member added there widens this automatically.
 */
export type ArtifactSkipReason = NonNullable<ArtifactRead['reason']>;

/**
 * One row of the project switcher from `GET /api/v1/projects` — a row of the
 * CONSOLE's own registry table (ARCHITECTURE.md, "Data-model additions (v3) →
 * RegisteredProject").
 *
 * DISTINCT from {@link Project}, and the two must never be substituted for one
 * another: `Project` is the read-through view of a project's contents on disk
 * (its manifest, its tickets), while this is the console's durable bookkeeping
 * about a project it TRACKS — a minted `id`, the `path` it was registered under,
 * `addedAt`, and whether it is `selected`. A row can exist for a directory that
 * no longer holds a project at all, which is exactly what `condition` reports.
 *
 * `condition` (never `availability`, and never a boolean) is T103's
 * `RegistryEntryCondition`, probed fresh on every read: `unreadable` /
 * `path_missing` / `not_a_project` / `no_factory_dir` / `ok`. `registered` is
 * `false` for the single reserved `session` row a `factory-console PATH` boot
 * prepends — a pin that was never added to anything, so it has no `addedAt`.
 */
export type RegisteredProjectOut = components['schemas']['RegisteredProjectOut'];

/** `{ items, total }` envelope of `GET /api/v1/projects`, unwrapped by `listProjects`. */
export type ProjectListResponse = components['schemas']['ProjectListResponse'];

/**
 * What the console is serving right now, from `GET /api/v1/projects/current` and
 * from the `PUT` that switches it — exactly one of `selected` (the row) and
 * `reason` (the named `SelectionFailure`) is set. Having nothing selected is a
 * 200 with a `reason`, never an error.
 */
export type CurrentSelection = components['schemas']['CurrentSelectionResponse'];
