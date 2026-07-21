# Contributing

Factory Console is a monorepo: a Python server (`server/factory_console/`) and a
SvelteKit frontend (`frontend/`) that builds into a single published wheel.
Day-to-day work runs through `make` (pass `PYTHON=python3.13` to run off a
virtualenv).

## Dev loop

Clone the repo and install both stacks, then install the git hook so linting and
formatting run on every commit:

```
git clone https://github.com/abbasihsn/factory-console.git
cd factory-console
pip install -e ".[dev]"        # Python: ruff + pytest + pre-commit
pnpm --dir frontend install    # frontend: eslint + prettier + Vitest + Playwright
pre-commit install
```

Then start the dev servers:

```
make dev
```

`make dev` runs Uvicorn with `--reload` alongside the Vite dev server, which
proxies `/api/*` to the Python port — so the SPA and API hot-reload together.

## Test loop

```
make test    # pytest (server) + pnpm test (frontend Vitest)
make lint    # ruff check + ruff format --check + eslint
```

The pre-commit hook — configured in
[`../.pre-commit-config.yaml`](../.pre-commit-config.yaml) — runs the same ruff +
eslint + prettier checks; CI re-runs them so a commit that skipped the local hook
is still caught.

## Packaging

```
make package  # build the SPA, copy it into server/factory_console/_static/, then build the wheel + sdist
make smoke    # build the wheel, install it in a throwaway venv, and curl /api/v1/health
```

## Release

Releases are cut by pushing a version tag:

```
git tag vX.Y.Z && git push origin vX.Y.Z
```

The tag triggers the release workflow (`release.yml`), which builds the wheel +
sdist and publishes to PyPI via OIDC trusted publishing plus a GitHub Release.
`make release` itself only prints this reminder — it does not publish.

## Track boundaries

The project is built across four tracks, each owning a distinct slice of the tree:

| Track | Owns |
|---|---|
| **foundation** | Repo skeleton, packaging, CI, Dockerfile, docs, the observability skeleton, and the dev/package/smoke scripts + `Makefile`. Everything outside the API/services/domain/file-adapter server tree and the frontend source tree. |
| **file-adapter** | The read-only `FileAdapter` port and its parsers (manifest, ticket `.md`, discovery, run-state), the `Real`/`Fake` adapters, and the fixture projects. This is the seam that owns every `open()`; the backend never touches `open()` directly. |
| **backend** | The FastAPI app factory, domain services, REST v1 endpoints, and the Typer CLI. Depends only on the domain models and the `FileAdapter` Protocol. |
| **frontend** | The SvelteKit SPA — shared components, routes, the generated API client, and Playwright e2e. Consumes REST v1 only; renders the server-provided `bodyHtml` rather than markdown in the browser. |

See [`architecture.md`](architecture.md) and
[`planning/ARCHITECTURE.md`](planning/ARCHITECTURE.md) for the contracts these
tracks share.
