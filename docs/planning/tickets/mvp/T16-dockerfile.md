# [T16] Multi-stage Dockerfile for reproducible builds

milestone: MVP · track: foundation · depends_on: T02, T03, T09 · provides: Multi-stage Dockerfile (node -> python builder -> python runtime) producing a hermetic `factory-console` image for CI and contributor parity. NOT the primary distribution

## Context

Per `ARCHITECTURE.md` tech_stack container-image entry: users install via `uvx`/`pipx`; a Dockerfile exists for reproducible/hermetic builds. Three stages: node stage builds SPA, python stage builds wheel with SPA baked in, thin runtime stage pip-installs. No docker-compose (single process).

## Staged approach

1. `Dockerfile` with three `FROM` stages.
   - Stage 1 `FROM node:20-alpine AS frontend-builder`: `WORKDIR /build/frontend`; `COPY frontend/package.json frontend/pnpm-lock.yaml ./`; `RUN corepack enable && pnpm install --frozen-lockfile`; `COPY frontend/ ./`; `RUN pnpm build`.
   - Stage 2 `FROM python:3.12-slim AS wheel-builder`: `WORKDIR /build`; `RUN pip install --no-cache-dir build`; `COPY pyproject.toml README.md LICENSE ./`; `COPY server/ ./server/`; `COPY --from=frontend-builder /build/frontend/build/ ./server/factory_console/_static/`; `RUN python -m build --wheel`.
   - Stage 3 `FROM python:3.12-slim AS runtime`: `RUN useradd -m -u 1000 fc`; `USER fc`; `WORKDIR /home/fc`; `COPY --from=wheel-builder /build/dist/*.whl /tmp/`; `RUN pip install --user --no-cache-dir /tmp/*.whl && rm /tmp/*.whl`; `ENV PATH=/home/fc/.local/bin:$PATH`; `EXPOSE 8000`; `ENTRYPOINT ["factory-console"]`; `CMD ["--host","127.0.0.1","--port","8000","--no-browser"]`.
2. `.dockerignore` excluding `.git, tests/, docs/planning/, .venv, node_modules, dist/, build/, server/factory_console/_static/` (regenerated), `*.pyc, __pycache__`.

## Critical files

- `Dockerfile`
- `.dockerignore`

## Interface & data

N/A — build artifact; the runtime image exposes the CLI contract transitively.

## Verification

`docker build -t factory-console:test .` succeeds; `docker run --rm factory-console:test --version` prints `0.1.0`; runtime image <200MB.
