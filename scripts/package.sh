#!/usr/bin/env bash
#
# Reproducible packaging recipe: build the SPA, copy it into the package's
# _static/ directory, then build the wheel + sdist. Called by CI's release job.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="${PYTHON:-python}"

# Build the static SPA bundle.
cd "$ROOT/frontend"
pnpm install --frozen-lockfile
pnpm build

# Copy the fresh bundle into the package (gitignored; populated only at package
# time). build/. copies contents including dotfiles and never trips on globbing.
STATIC_DIR="$ROOT/server/factory_console/_static"
rm -rf "$STATIC_DIR"
mkdir -p "$STATIC_DIR"
cp -R build/. "$STATIC_DIR/"

# Build the distributables; setuptools bundles _static/ via package-data.
cd "$ROOT"
"$PYTHON" -m build --wheel --sdist
