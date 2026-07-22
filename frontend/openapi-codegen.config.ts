/**
 * Reference documentation for the `pnpm codegen` pipeline.
 *
 * This module is NOT executed by the openapi-typescript CLI — the CLI takes its
 * arguments straight from the `codegen` script in `package.json`. It exists so
 * the codegen settings live in one typed, discoverable place.
 *
 * `pnpm codegen` runs two steps:
 *   1. `openapi-typescript <url> -o src/lib/api/types.ts --immutable` regenerates
 *      `src/lib/api/types.ts` from the server's OpenAPI document (as `readonly`
 *      shapes, because of `--immutable`).
 *   2. `node scripts/postcodegen.mjs` prepends the DO-NOT-EDIT banner so the
 *      generated file is clearly marked as machine-owned.
 *
 * The source URL is overridable with the `FC_OPENAPI_URL` env var (e.g. to point
 * at a saved `openapi.json` file); it defaults to the running dev backend.
 */
export interface OpenApiCodegenConfig {
	/**
	 * The exact source argument passed to openapi-typescript — a shell expansion
	 * that honours `FC_OPENAPI_URL` and falls back to {@link defaultUrl}.
	 */
	readonly urlArg: string;
	/** Env var that overrides the OpenAPI source URL. */
	readonly urlEnvVar: string;
	/** Source URL used when `FC_OPENAPI_URL` is unset. */
	readonly defaultUrl: string;
	/** Output path for the generated types, relative to `frontend/`. */
	readonly output: string;
	/** Whether `--immutable` is passed (emits `readonly` properties). */
	readonly immutable: boolean;
	/** DO-NOT-EDIT banner marker prepended by `scripts/postcodegen.mjs`. */
	readonly banner: string;
}

const DEFAULT_OPENAPI_URL = 'http://127.0.0.1:8000/api/v1/openapi.json';

export const openApiCodegenConfig: OpenApiCodegenConfig = {
	urlArg: '${FC_OPENAPI_URL:-http://127.0.0.1:8000/api/v1/openapi.json}',
	urlEnvVar: 'FC_OPENAPI_URL',
	defaultUrl: DEFAULT_OPENAPI_URL,
	output: 'src/lib/api/types.ts',
	immutable: true,
	banner: 'DO NOT EDIT — regenerate with pnpm codegen'
};
