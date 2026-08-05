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
 */
export type RunRecord = components['schemas']['RunRecord'];

/**
 * One artifact read: `data` when it parsed, `reason` when it did not — exactly one
 * of the two, enforced server-side.
 *
 * `data` is DELIBERATELY untyped (`{ [key: string]: unknown }`): the server models
 * no field inside a factory artifact, because it has none it has verified against a
 * real captured file. Read fields out of it defensively — a key may be missing or
 * any type at all — and never widen this into a hand-written schema here; that
 * would put back the guesswork the backend refused to ship.
 */
export type ArtifactRead = components['schemas']['ArtifactRead'];

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
