# Multi-stage build for a lean, self-contained `factory-console` image.
#
# Mirrors scripts/package.sh: build the SPA, bake it into the package's
# _static/ directory, then build and install the wheel. Three stages keep the
# node/build toolchains out of the final image (thin slim-python runtime only).
#
#   1. frontend-builder — node: build the Svelte SPA (static output).
#   2. wheel-builder     — python: bake the SPA into _static/, build the wheel.
#   3. runtime           — python-slim: pip-install the wheel as a non-root user.
#
# Base images are pinned to major-version tags (node:22-alpine, python:3.12-slim)
# rather than digests: this image is a convenience runtime, not the primary
# distribution (see ARCHITECTURE.md), so it favors picking up upstream security
# patches over bit-for-bit reproducibility.

# --- Stage 1: build the static SPA ------------------------------------------
FROM node:22-alpine AS frontend-builder
WORKDIR /build/frontend

# Dependency manifests first so the install layer caches independently of source
# churn. pnpm-workspace.yaml carries the `allowBuilds` approval (esbuild) that
# pnpm needs at install time to run that dependency's build script.
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
# Pin pnpm explicitly (there is no packageManager field in frontend/package.json)
# so the install is hermetic and stays on pnpm 11 like CI, rather than drifting
# with whatever pnpm the base image's corepack happens to default to.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable && corepack prepare pnpm@11.15.1 --activate && pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build

# --- Stage 2: build the wheel with the SPA baked in -------------------------
FROM python:3.12-slim AS wheel-builder
WORKDIR /build
RUN pip install --no-cache-dir build

COPY pyproject.toml README.md LICENSE ./
COPY server/ ./server/
# Bake the built SPA into the package before building the wheel; setuptools
# bundles it via [tool.setuptools.package-data] "_static/**/*".
COPY --from=frontend-builder /build/frontend/build/ ./server/factory_console/_static/
RUN python -m build --wheel

# --- Stage 3: thin runtime ---------------------------------------------------
FROM python:3.12-slim AS runtime

# Run as a non-root user.
RUN useradd -m -u 1000 fc
USER fc
WORKDIR /home/fc

# Install the wheel into the user site. --chown makes the copied .whl fc-owned
# so the non-root user can delete it afterward: /tmp is sticky (1777), and a
# root-owned file there is not removable by fc.
COPY --from=wheel-builder --chown=fc:fc /build/dist/*.whl /tmp/
RUN pip install --user --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
ENV PATH=/home/fc/.local/bin:$PATH

# HOME pinned rather than left to the runtime's /etc/passwd lookup, because from
# v3.0 it decides where writable state lands: the console's own registry defaults
# to ~/.factory-console/console.db, and /home/fc is the one directory the fc user
# owns (WORKDIR below is a bind mount of someone else's project).
ENV HOME=/home/fc

# --- The console's own registry: this image stays single-project ------------
#
# v3.0 gives the console a store of its OWN (ARCHITECTURE.md, "Console-owned
# store"): a SQLite registry of projects, created lazily the first time a
# registry endpoint is called — which the browser UI's project dropdown does on
# every page load. Deliberately NOT declared here: no VOLUME, and no
# FACTORY_CONSOLE_DB_PATH override.
#
# The reason is the image's actual usage pattern. It serves the ONE project
# bind-mounted at /project for the life of one `docker run --rm` (see the note
# below); there is no long-running multi-project serve mode yet — that is v3's
# LATER work — so a registry that outlives the container has nothing to be about.
# A VOLUME would also mint an anonymous volume per invocation for a file the
# container never reads twice, which is a leak, not persistence.
#
# What this leaves is honest rather than broken: HOME above is fc-owned, so the
# lazy creation succeeds at 0700/0600 like anywhere else (nothing crashes), and
# the registry lives and dies inside the container's writable layer. To keep one
# across restarts anyway, mount your own storage over it — the mount must be
# writable by uid 1000, since nothing in the image pre-creates that directory for
# Docker to copy fc's ownership from:
#
#   docker run ... -v "$PWD/console-store:/home/fc/.factory-console" ...   # chown 1000 it first
#
# The container never needs that to serve the project it was pointed at.

# The console serves the project in its working directory, so the target project
# must be bind-mounted at this WORKDIR — nothing is baked into the image
# (docs/planning is .dockerignore'd). Without the mount, discover_project finds no
# tickets.json and exits 1, so a bare `docker run <image>` cannot serve by design.
WORKDIR /project

# No EXPOSE: the console binds 127.0.0.1 by design — the loopback trust boundary
# enforced by the host validator (config.require_loopback_host, which refuses
# 0.0.0.0) — so a bridge-network `-p 8000:8000` publish can never reach it. To use
# the served UI, run on the host loopback with the project mounted at /project:
#
#   docker run --rm --network host -v "$PWD:/project" factory-console   # from a project dir
#
# (Linux; on other hosts reach it via `docker exec`.) `--version` and other CLI use
# override CMD and need neither a mount nor networking. The image is a
# convenience/CLI artifact, not the primary distribution (see ARCHITECTURE.md).
ENTRYPOINT ["factory-console"]
CMD ["--host", "127.0.0.1", "--port", "8000", "--no-browser"]
