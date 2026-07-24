# [T46] Extend the typed api client for the v1 read endpoints (regenerate types + graph/search/roadmap wrappers)

milestone: v1 · track: frontend · depends_on: T41, T42, T43, T28 · provides: regenerated src/lib/api/types.ts + getGraph()/searchTickets()/expanded getRoadmap() wrappers + model aliases so the v1 routes consume the backend contracts type-safely

## Context

The `/graph`, `/roadmap` and `/search` routes need typed access to three new/expanded backend endpoints (T41/T42/T43). This ticket is the single owner of the api aggregation surface (`types.ts`, `models.ts`, `client.ts`, `index.ts`) so the three feature routes that follow never collide on those shared files and never each re-run codegen. It regenerates the OpenAPI-derived types once and adds thin same-origin fetch wrappers mirroring the existing `getProject`/`listTickets` style.

## Staged approach

1. With a backend serving the v1 endpoints, run `pnpm codegen` (`openapi-typescript` against `${FC_OPENAPI_URL}`) to regenerate `frontend/src/lib/api/types.ts` — DO NOT hand-edit it (banner enforced by `scripts/postcodegen.mjs`).
2. In `frontend/src/lib/api/models.ts`: add aliases for the new generated schemas (`TicketGraph`/graph, `SearchResponse`/`SearchHit`) and update the `Roadmap` alias to the expanded generated schema (`Roadmap | RoadmapAbsent`); per the standing TODO in that file, also swap the temporary hand-written `DepNeighborhood` for the now-generated schema (incidental, must keep the existing deps route compiling).
3. In `frontend/src/lib/api/client.ts`: add `getGraph(): Promise<TicketGraph>` (GET graph), `searchTickets(params): Promise<SearchHit[]>` (GET search?q=..., envelope-unwrapped like `listTickets`), and update `getRoadmap()`'s return type to the expanded `Roadmap` — all following the existing same-origin `request<T>` helper (no leading slash, `ApiError` on non-2xx). Add a `SearchParams` interface next to `ListTicketsParams`.
4. In `frontend/src/lib/api/index.ts`: re-export the new wrappers and the new types.
5. Extend `frontend/src/lib/api/client.test.ts` + contracts/models tests for the new wrappers.

## Critical files

- `frontend/src/lib/api/types.ts` (regenerated — do not hand-edit)
- `frontend/src/lib/api/models.ts` (aliases)
- `frontend/src/lib/api/client.ts` (wrappers)
- `frontend/src/lib/api/index.ts` (re-exports)

## Interface & data

- `getGraph() -> TicketGraph`; `searchTickets({ q }: SearchParams) -> SearchHit[]` (drops the list envelope like `listTickets`); `getRoadmap() -> Roadmap` (now the expanded present/absent union).
- Touched BY REFERENCE (backend is the source of truth, consumed via regenerated `types.ts`): the new `TicketGraph` (T42), `SearchResponse`/`SearchHit` (T41), and expanded `Roadmap` (T43) schemas. Existing schemas re-aliased: `TicketSummary`, `RunState`, `DepNeighborhood`.
- DB ops: N/A (read-only REST). NFR: same-origin only (client refuses absolute URLs), request timeout via `AbortSignal.timeout`, no auth (127.0.0.1), no cache.

## Verification

Boot a backend serving the v1 endpoints, `pnpm codegen`, then `pnpm check` (svelte-check/tsc strict) passes with the new wrappers and no `any`; `pnpm test` (vitest) green including the new client wrapper tests; `pnpm lint`. Confirm `types.ts` still carries the DO-NOT-EDIT banner.
